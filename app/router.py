import json
import logging
import mimetypes
import time
import uuid
from typing import Optional, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import verify_api_key
from app.models import ChatRequest, ChatResponse, ContentPart, HealthResponse, ImageGenerateRequest, Message, ModelsResponse, ResponseRequest
from app.provider_config import get_config
from app.providers.azure import AzureFoundryProvider
from app.providers.bedrock import BedrockProvider
from app.providers.openai import OpenAIProvider
from app.providers.gemini import GeminiProvider
from app.stats import get_collector

logger = logging.getLogger(__name__)

router = APIRouter()

_providers = {
    "bedrock": BedrockProvider(),
    "openai": OpenAIProvider(),
    "gemini": GeminiProvider(),
    "azure": AzureFoundryProvider(),
}


def _get_provider(provider_name: str):
    if provider_name not in _providers:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider_name}'. Available: {list(_providers.keys())}")
    return _providers[provider_name]


def _detect_provider(model: str) -> str:
    """Auto-detect provider name from model ID."""
    config = get_config()
    for m in config.openai.models:
        if m.strip().lower() == model.lower():
            return "openai"
    for m in config.gemini.models:
        if m.strip().lower() == model.lower():
            return "gemini"
    for m_id in config.bedrock.models:
        if m_id.strip().lower() == model.lower():
            return "bedrock"
    for m in config.azure.models:
        if m.strip().lower() == model.lower():
            return "azure"
    raise HTTPException(status_code=400, detail=f"Model '{model}' not found in any provider config")


def _record_stats(result, provider_name: str, model: str):
    stats = get_collector()
    stats.record(
        provider=provider_name,
        model=model,
        input_tokens=result.usage.input_tokens if result.usage else 0,
        output_tokens=result.usage.output_tokens if result.usage else 0,
    )


def _openai_chat_usage(provider_name: str, result: ChatResponse) -> dict:
    """Build an OpenAI-standard chat usage object, preserving upstream details.

    Azure: raw_usage is already OpenAI shape (prompt_tokens/completion_tokens/_details) — pass through.
    Bedrock (Claude): raw_usage is Anthropic shape (input_tokens/output_tokens/cache_*) — map.
    Gemini: raw_usage is google shape (prompt_token_count/candidates_token_count/cached_content_token_count/thoughts_token_count) — map.
    """
    raw = result.raw_usage or {}
    in_tok = result.usage.input_tokens if result.usage else 0
    out_tok = result.usage.output_tokens if result.usage else 0
    total = in_tok + out_tok

    if provider_name == "azure":
        usage = dict(raw) if raw else {}
        usage.setdefault("prompt_tokens", in_tok)
        usage.setdefault("completion_tokens", out_tok)
        usage.setdefault("total_tokens", total)
        return usage

    if provider_name == "bedrock":
        cache_create = int(raw.get("cache_creation_input_tokens") or 0)
        cache_read = int(raw.get("cache_read_input_tokens") or 0)
        usage = {
            "prompt_tokens": in_tok + cache_create + cache_read,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + cache_create + cache_read + out_tok,
        }
        if cache_read or cache_create:
            usage["prompt_tokens_details"] = {
                "cached_tokens": cache_read,
                "cache_creation_input_tokens": cache_create,
            }
        return usage

    if provider_name == "gemini":
        cached = int(raw.get("cached_content_token_count") or 0)
        thoughts = int(raw.get("thoughts_token_count") or 0)
        usage = {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": int(raw.get("total_token_count") or total),
        }
        if cached:
            usage["prompt_tokens_details"] = {"cached_tokens": cached}
        if thoughts:
            usage["completion_tokens_details"] = {"reasoning_tokens": thoughts}
        return usage

    return {
        "prompt_tokens": in_tok,
        "completion_tokens": out_tok,
        "total_tokens": total,
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: str = Depends(verify_api_key)) -> ChatResponse:
    provider = _get_provider(request.provider)
    try:
        result = await provider.chat(request)
        _record_stats(result, request.provider, request.model)
        return result
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Provider error for model=%s", request.model if hasattr(request, 'model') else "unknown")
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/models")
async def list_models(provider: str = "bedrock", _: str = Depends(verify_api_key)):
    p = _get_provider(provider)
    return {"provider": provider, "models": p.list_models()}


# ---- OpenAI-compatible endpoints (for new-api / One API upstream) ----

class OAIMessage(BaseModel):
    role: str
    content: Union[str, list[dict]]


class OAIRequest(BaseModel):
    model: str
    messages: list[OAIMessage]
    max_tokens: int = 512
    max_completion_tokens: int = 0
    temperature: float = 0.7
    reasoning_effort: str = ""
    stream: bool = False


@router.post("/v1/chat/completions")
async def chat_completions(request: OAIRequest, _: str = Depends(verify_api_key)):
    provider_name = _detect_provider(request.model)
    messages = []
    for m in request.messages:
        if isinstance(m.content, list):
            parts = [ContentPart(**p) for p in m.content]
            messages.append(Message(role=m.role, content=parts))
        else:
            messages.append(Message(role=m.role, content=m.content))
    req = ChatRequest(
        provider=provider_name,
        model=request.model,
        messages=messages,
        max_tokens=request.max_tokens,
        max_completion_tokens=request.max_completion_tokens,
        temperature=request.temperature,
        reasoning_effort=request.reasoning_effort,
        stream=request.stream,
    )
    provider = _get_provider(provider_name)
    try:
        result = await provider.chat(req)
        _record_stats(result, provider_name, request.model)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Provider error for model=%s", request.model if hasattr(request, 'model') else "unknown")
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    # Build message content — multimodal if images are present
    if result.images:
        message_content = []
        if result.content:
            message_content.append({"type": "text", "text": result.content})
        for img in result.images:
            message_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img.mime_type};base64,{img.data}"},
            })
    else:
        message_content = result.content

    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": message_content},
            "finish_reason": "stop",
        }],
        "usage": _openai_chat_usage(provider_name, result),
    }


@router.get("/v1/models")
async def list_models_openai(_: str = Depends(verify_api_key)):
    config = get_config()
    data = []
    now = int(time.time())
    for m_id in config.bedrock.models:
        data.append({"id": m_id, "object": "model", "created": now, "owned_by": "bedrock"})
    for m in config.openai.models:
        if m.strip():
            data.append({"id": m.strip(), "object": "model", "created": now, "owned_by": "openai"})
    for m in config.gemini.models:
        if m.strip():
            data.append({"id": m.strip(), "object": "model", "created": now, "owned_by": "gemini"})
    for m in config.azure.models:
        if m.strip():
            data.append({"id": m.strip(), "object": "model", "created": now, "owned_by": "azure"})
    return {"object": "list", "data": data}


# ---- Gemini native API endpoints (for One API Gemini channel) ----

class GeminiNativeRequest(BaseModel):
    contents: list[dict] = []
    systemInstruction: dict | None = None
    generationConfig: dict | None = None


def _gemini_usage_metadata(result: ChatResponse) -> dict:
    """Build Gemini-native usageMetadata, preserving cached/thoughts counts when present."""
    raw = result.raw_usage or {}
    in_tok = result.usage.input_tokens if result.usage else 0
    out_tok = result.usage.output_tokens if result.usage else 0
    total = int(raw.get("total_token_count") or (in_tok + out_tok))

    md = {
        "promptTokenCount": in_tok,
        "candidatesTokenCount": out_tok,
        "totalTokenCount": total,
    }
    cached = int(raw.get("cached_content_token_count") or 0)
    thoughts = int(raw.get("thoughts_token_count") or 0)
    if cached:
        md["cachedContentTokenCount"] = cached
    if thoughts:
        md["thoughtsTokenCount"] = thoughts
    return md


def _gemini_response(result: ChatResponse, model: str, finish_reason: str = "STOP") -> dict:
    """Convert internal ChatResponse to Gemini native response format."""
    parts = []
    if result.content:
        parts.append({"text": result.content})
    if result.images:
        for img in result.images:
            parts.append({
                "inlineData": {"mimeType": img.mime_type, "data": img.data}
            })
    return {
        "candidates": [{
            "content": {"role": "model", "parts": parts},
            "finishReason": finish_reason,
        }],
        "usageMetadata": _gemini_usage_metadata(result),
    }


def _build_gemini_messages(request: GeminiNativeRequest) -> list[Message]:
    """Build internal Message list from Gemini native request contents."""
    messages = []
    if request.systemInstruction:
        parts = request.systemInstruction.get("parts", [])
        sys_text = " ".join(p.get("text", "") for p in parts if p.get("text"))
        if sys_text.strip():
            messages.append(Message(role="system", content=sys_text.strip()))

    for c in request.contents:
        role = c.get("role", "user")
        parts = c.get("parts", [])
        content_parts = []
        for p in parts:
            if "text" in p:
                content_parts.append({"type": "text", "text": p["text"]})
            elif "inlineData" in p:
                inline = p["inlineData"]
                mime = inline.get("mimeType", "image/png")
                data = inline.get("data", "")
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}"}
                })
        if content_parts:
            messages.append(Message(role=role, content=[ContentPart(**cp) for cp in content_parts]))
        elif parts and "text" in parts[0]:
            messages.append(Message(role=role, content=parts[0]["text"]))
        else:
            messages.append(Message(role=role, content=""))
    return messages


@router.post("/v1beta/models/{model_path:path}")
async def gemini_generate_content(model_path: str, request: GeminiNativeRequest, _: str = Depends(verify_api_key)):
    is_stream = model_path.endswith(":streamGenerateContent")
    if is_stream:
        model = model_path.rsplit(":streamGenerateContent", 1)[0]
    elif model_path.endswith(":generateContent"):
        model = model_path.rsplit(":generateContent", 1)[0]
    else:
        raise HTTPException(status_code=404, detail=f"Unknown action for model path: {model_path}")

    provider_name = _detect_provider(model)
    messages = _build_gemini_messages(request)
    gc = request.generationConfig or {}
    req = ChatRequest(
        provider=provider_name,
        model=model,
        messages=messages,
        max_tokens=gc.get("maxOutputTokens", 512),
        temperature=gc.get("temperature", 0.7),
    )
    provider = _get_provider(provider_name)

    async def _do_chat() -> ChatResponse:
        try:
            result = await provider.chat(req)
            _record_stats(result, provider_name, model)
            return result
        except NotImplementedError as e:
            raise HTTPException(status_code=501, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("Provider error for model=%s", model)
            raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    if is_stream:
        async def event_stream():
            result = await _do_chat()
            resp = _gemini_response(result, model)
            yield f"data: {json.dumps(resp)}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
    else:
        result = await _do_chat()
        return _gemini_response(result, model)


@router.get("/v1beta/models")
async def list_models_gemini(_: str = Depends(verify_api_key)):
    config = get_config()
    models_list = []
    for m in config.gemini.models:
        if m.strip():
            models_list.append({
                "name": f"models/{m.strip()}",
                "displayName": m.strip(),
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            })
    return {"models": models_list}


# ---- Anthropic Messages API-compatible endpoint ----

class AnthropicContentBlock(BaseModel):
    type: str
    text: str = ""


class AnthropicMessage(BaseModel):
    role: str
    content: Union[str, list[AnthropicContentBlock]]


class AnthropicRequest(BaseModel):
    model: str
    max_tokens: int
    messages: list[AnthropicMessage]
    system: Union[str, list[dict], None] = None
    temperature: float = 1.0


def _anthropic_content_to_str(content: Union[str, list[AnthropicContentBlock]]) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if hasattr(block, 'text') and block.text:
            parts.append(block.text)
    return "\n".join(parts)


def _anthropic_usage(provider_name: str, result: ChatResponse) -> dict:
    """Build an Anthropic-standard usage object: input_tokens / output_tokens / cache_*."""
    raw = result.raw_usage or {}
    in_tok = result.usage.input_tokens if result.usage else 0
    out_tok = result.usage.output_tokens if result.usage else 0

    if provider_name == "bedrock":
        usage = {"input_tokens": in_tok, "output_tokens": out_tok}
        if raw.get("cache_creation_input_tokens"):
            usage["cache_creation_input_tokens"] = int(raw["cache_creation_input_tokens"])
        if raw.get("cache_read_input_tokens"):
            usage["cache_read_input_tokens"] = int(raw["cache_read_input_tokens"])
        return usage

    if provider_name == "azure":
        cached = 0
        details = raw.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or 0)
        usage = {"input_tokens": in_tok, "output_tokens": out_tok}
        if cached:
            usage["cache_read_input_tokens"] = cached
        return usage

    if provider_name == "gemini":
        cached = int(raw.get("cached_content_token_count") or 0)
        usage = {"input_tokens": in_tok, "output_tokens": out_tok}
        if cached:
            usage["cache_read_input_tokens"] = cached
        return usage

    return {"input_tokens": in_tok, "output_tokens": out_tok}


@router.post("/v1/messages")
async def messages_create(request: AnthropicRequest, _: str = Depends(verify_api_key)):
    provider_name = _detect_provider(request.model)

    # Build messages list for internal ChatRequest
    messages = []
    for msg in request.messages:
        content_str = _anthropic_content_to_str(msg.content)
        messages.append(Message(role=msg.role, content=content_str))

    # Prepend system prompt as a system message if provided
    if request.system:
        system_text = request.system if isinstance(request.system, str) else str(request.system)
        if system_text.strip():
            messages.insert(0, Message(role="system", content=system_text.strip()))

    req = ChatRequest(
        provider=provider_name,
        model=request.model,
        messages=messages,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        stream=False,
    )
    provider = _get_provider(provider_name)
    try:
        result = await provider.chat(req)
        _record_stats(result, provider_name, request.model)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Provider error for model=%s", request.model if hasattr(request, 'model') else "unknown")
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    return {
        "id": "msg_" + uuid.uuid4().hex[:24],
        "type": "message",
        "role": "assistant",
        "model": request.model,
        "content": [{"type": "text", "text": result.content}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": _anthropic_usage(provider_name, result),
    }


# ---- OpenAI Responses API-compatible endpoint ----


class OAIResponseRequest(BaseModel):
    model: str
    input: str = ""
    max_output_tokens: int = 512
    reasoning_effort: str = ""


def _openai_responses_usage(provider_name: str, result: ChatResponse) -> dict:
    """Build an OpenAI-standard /v1/responses usage object."""
    raw = result.raw_usage or {}
    in_tok = result.usage.input_tokens if result.usage else 0
    out_tok = result.usage.output_tokens if result.usage else 0

    if provider_name == "azure" and raw:
        usage = dict(raw)
        usage.setdefault("input_tokens", in_tok)
        usage.setdefault("output_tokens", out_tok)
        usage.setdefault("total_tokens", in_tok + out_tok)
        return usage

    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
    }


@router.post("/v1/responses")
async def responses_create(request: OAIResponseRequest, _: str = Depends(verify_api_key)):
    provider_name = _detect_provider(request.model)
    req = ResponseRequest(
        provider=provider_name,
        model=request.model,
        input=request.input,
        max_output_tokens=request.max_output_tokens,
        reasoning_effort=request.reasoning_effort,
    )
    provider = _get_provider(provider_name)
    try:
        result = await provider.responses(req)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Provider error for model=%s", request.model)
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    return {
        "id": "resp_" + uuid.uuid4().hex[:24],
        "object": "response",
        "created_at": int(time.time()),
        "model": request.model,
        "output": [{
            "type": "message",
            "content": [{"type": "output_text", "text": result.content}],
        }],
        "usage": _openai_responses_usage(provider_name, result),
    }


# ---- OpenAI Images API-compatible endpoint ----


class OAIImageRequest(BaseModel):
    model: str
    prompt: str
    n: int = 1
    size: str = ""
    quality: str = ""
    response_format: str = ""
    output_format: str = ""
    output_compression: int | None = None
    background: str = ""

    class Config:
        extra = "allow"


@router.post("/v1/images/generations")
async def images_generations(request: OAIImageRequest, _: str = Depends(verify_api_key)):
    provider_name = _detect_provider(request.model)
    known = {
        "model", "prompt", "n", "size", "quality", "response_format",
        "output_format", "output_compression", "background",
    }
    extra = {k: v for k, v in request.model_dump().items() if k not in known}
    req = ImageGenerateRequest(
        provider=provider_name,
        model=request.model,
        prompt=request.prompt,
        n=request.n,
        size=request.size,
        quality=request.quality,
        response_format=request.response_format,
        output_format=request.output_format,
        output_compression=request.output_compression,
        background=request.background,
        extra=extra,
    )
    provider = _get_provider(provider_name)
    if not hasattr(provider, "images"):
        raise HTTPException(status_code=501, detail=f"Provider '{provider_name}' does not support image generation")
    try:
        result = await provider.images(req)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Provider error for model=%s", request.model)
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    if result.usage:
        get_collector().record(
            provider=provider_name,
            model=request.model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )

    body = {
        "created": int(time.time()),
        "data": [{"b64_json": img.data} for img in result.images],
    }
    if result.raw_meta:
        for k, v in result.raw_meta.items():
            body.setdefault(k, v)
    if result.raw_usage:
        body["usage"] = result.raw_usage
    return body


def _guess_image_mime(filename: str | None) -> str:
    if filename:
        m, _ = mimetypes.guess_type(filename)
        if m:
            return m
    return "image/png"


@router.post("/v1/images/edits")
async def images_edits(
    request: Request,
    _: str = Depends(verify_api_key),
    prompt: str = Form(...),
    model: str = Form(...),
    n: int = Form(1),
    size: str = Form(""),
    response_format: str = Form(""),
    output_format: str = Form(""),
    output_compression: Optional[int] = Form(None),
    quality: str = Form(""),
    background: str = Form(""),
    user: str = Form(""),
    mask: Optional[UploadFile] = File(None),
):
    form = await request.form()
    image_files: list = []

    def _extract(key: str):
        for v in form.getlist(key):
            if hasattr(v, "read") and hasattr(v, "filename"):
                image_files.append(v)

    _extract("image")
    if not image_files:
        _extract("image[]")
    if not image_files:
        for k in form.keys():
            if k.startswith("image["):
                _extract(k)

    if not image_files:
        try:
            keys_dump = [(k, type(v).__name__) for k, v in form.multi_items()]
        except Exception:
            keys_dump = list(form.keys())
        logger.warning("images_edits: 'image' not found in form. keys=%s", keys_dump)
        raise HTTPException(status_code=400, detail=f"'image' field is required (received form keys: {keys_dump})")

    images_payload: list[tuple[str, bytes, str]] = []
    for f in image_files:
        data = await f.read()
        images_payload.append((f.filename or "image.png", data, _guess_image_mime(f.filename)))

    mask_payload: tuple[str, bytes, str] | None = None
    if mask is not None:
        mb = await mask.read()
        if mb:
            mask_payload = (mask.filename or "mask.png", mb, _guess_image_mime(mask.filename))

    provider_name = _detect_provider(model)
    provider = _get_provider(provider_name)
    if not hasattr(provider, "images_edit"):
        raise HTTPException(status_code=501, detail=f"Provider '{provider_name}' does not support image edits")

    try:
        result = await provider.images_edit(
            model=model,
            prompt=prompt,
            images=images_payload,
            mask=mask_payload,
            n=n,
            size=size,
            response_format=response_format,
            output_format=output_format,
            output_compression=output_compression,
            quality=quality,
            background=background,
            user=user,
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Provider error for model=%s", model)
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")

    if result.usage:
        get_collector().record(
            provider=provider_name,
            model=model,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )

    body = {
        "created": int(time.time()),
        "data": [{"b64_json": img.data} for img in result.images],
    }
    if result.raw_meta:
        for k, v in result.raw_meta.items():
            body.setdefault(k, v)
    if result.raw_usage:
        body["usage"] = result.raw_usage
    return body
