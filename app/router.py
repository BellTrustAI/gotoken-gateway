import logging
import time
import uuid
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth import verify_api_key
from app.models import ChatRequest, ChatResponse, ContentPart, HealthResponse, Message, ModelsResponse, ResponseRequest
from app.provider_config import get_config
from app.providers.azure import AzureFoundryProvider
from app.providers.bedrock import BedrockProvider
from app.providers.openai import OpenAIProvider
from app.providers.gemini import GeminiProvider

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


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: str = Depends(verify_api_key)) -> ChatResponse:
    provider = _get_provider(request.provider)
    try:
        return await provider.chat(request)
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
        "usage": {
            "prompt_tokens": result.usage.input_tokens if result.usage else 0,
            "completion_tokens": result.usage.output_tokens if result.usage else 0,
            "total_tokens": (result.usage.input_tokens + result.usage.output_tokens) if result.usage else 0,
        },
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
        "usage": {
            "input_tokens": result.usage.input_tokens if result.usage else 0,
            "output_tokens": result.usage.output_tokens if result.usage else 0,
        },
    }


# ---- OpenAI Responses API-compatible endpoint ----


class OAIResponseRequest(BaseModel):
    model: str
    input: str = ""
    max_output_tokens: int = 512
    reasoning_effort: str = ""


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
        "usage": {
            "input_tokens": result.usage.input_tokens if result.usage else 0,
            "output_tokens": result.usage.output_tokens if result.usage else 0,
        },
    }
