import asyncio
import logging

from azure.identity import ClientSecretCredential, get_bearer_token_provider
from openai import OpenAI

from app.models import ChatRequest, ChatResponse, ImageGenerateRequest, ImageGenerateResponse, ImageOutput, ResponseRequest, Usage
from app.provider_config import get_config

logger = logging.getLogger(__name__)

_AZURE_AD_SCOPE = "https://ai.azure.com/.default"

# Models that only support the Responses API (not Chat Completions)
_RESPONSES_ONLY_MODELS = {"gpt-5.4-pro", "gpt-5.2-codex", "gpt-5.3-codex"}


class AzureFoundryProvider:
    def __init__(self) -> None:
        self._client = None
        self._entra_client = None
        self._models: list[str] = []

    def _ensure_clients(self):
        cfg = get_config().azure
        self._models = [m.strip() for m in cfg.models if m.strip()]

        # Always create API key client
        self._client = OpenAI(
            api_key=cfg.api_key,
            base_url=cfg.endpoint,
        )

        # Also create Entra ID client if configured
        if cfg.use_entra_id and cfg.entra_tenant_id:
            credential = ClientSecretCredential(
                tenant_id=cfg.entra_tenant_id,
                client_id=cfg.entra_client_id,
                client_secret=cfg.entra_client_secret,
            )
            token_provider = get_bearer_token_provider(
                credential, _AZURE_AD_SCOPE
            )
            self._entra_client = OpenAI(
                api_key=token_provider,
                base_url=cfg.endpoint,
            )
        else:
            self._entra_client = None

    def _pick_client(self) -> OpenAI:
        return self._client

    def list_models(self) -> list[str]:
        self._ensure_clients()
        return list(self._models)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self._ensure_clients()
        if request.model not in self._models:
            available = ", ".join(self._models)
            raise ValueError(f"Unknown Azure model '{request.model}'. Available: {available}")

        # Auto-route to Responses API for models that don't support Chat Completions
        if request.model.lower() in _RESPONSES_ONLY_MODELS:
            return await self._chat_via_responses(request)

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        is_codex = "codex" in request.model.lower()

        kwargs: dict = {
            "model": request.model,
            "messages": messages,
            "max_completion_tokens": request.max_completion_tokens or request.max_tokens,
        }

        if is_codex:
            if request.reasoning_effort:
                kwargs["reasoning_effort"] = request.reasoning_effort
        else:
            if request.temperature > 0:
                kwargs["temperature"] = request.temperature

        client = self._pick_client()
        response = await asyncio.to_thread(
            client.chat.completions.create, **kwargs
        )

        choice = response.choices[0]
        return ChatResponse(
            provider="azure",
            model=request.model,
            content=choice.message.content or "",
            usage=Usage(
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
            ),
        )

    async def _chat_via_responses(self, request: ChatRequest) -> ChatResponse:
        """Convert a ChatRequest to a Responses API call for models that need it."""
        parts = []
        for m in request.messages:
            if m.role == "system":
                parts.append(f"System: {m.content}")
            elif m.role == "user":
                parts.append(f"User: {m.content}")
            elif m.role == "assistant":
                parts.append(f"Assistant: {m.content}")
        input_text = "\n\n".join(parts)

        resp_req = ResponseRequest(
            provider="azure",
            model=request.model,
            input=input_text,
            max_output_tokens=request.max_completion_tokens or request.max_tokens,
            reasoning_effort=request.reasoning_effort,
        )
        return await self.responses(resp_req)

    async def responses(self, request: ResponseRequest) -> ChatResponse:
        self._ensure_clients()
        if request.model not in self._models:
            available = ", ".join(self._models)
            raise ValueError(f"Unknown Azure model '{request.model}'. Available: {available}")

        kwargs: dict = {
            "model": request.model,
            "input": request.input,
            "max_output_tokens": request.max_output_tokens,
        }
        if request.reasoning_effort:
            kwargs["reasoning_effort"] = request.reasoning_effort

        client = self._pick_client()
        response = await asyncio.to_thread(
            client.responses.create, **kwargs
        )

        output_text = ""
        if response.output:
            for item in response.output:
                if getattr(item, "type", None) == "message":
                    if hasattr(item, "content") and item.content:
                        output_text = item.content[0].text or ""
                    break

        return ChatResponse(
            provider="azure",
            model=request.model,
            content=output_text,
            usage=Usage(
                input_tokens=response.usage.input_tokens if response.usage else 0,
                output_tokens=response.usage.output_tokens if response.usage else 0,
            ),
        )

    async def images(self, request: ImageGenerateRequest) -> ImageGenerateResponse:
        self._ensure_clients()
        if request.model not in self._models:
            available = ", ".join(self._models)
            raise ValueError(f"Unknown Azure model '{request.model}'. Available: {available}")

        kwargs: dict = {
            "model": request.model,
            "prompt": request.prompt,
            "n": request.n or 1,
        }
        if request.size:
            kwargs["size"] = request.size
        if request.quality:
            kwargs["quality"] = request.quality
        if request.response_format:
            kwargs["response_format"] = request.response_format
        if request.output_format:
            kwargs["output_format"] = request.output_format
        if request.output_compression is not None:
            kwargs["output_compression"] = request.output_compression
        if request.background:
            kwargs["background"] = request.background
        if request.extra:
            for k, v in request.extra.items():
                kwargs.setdefault(k, v)

        client = self._pick_client()
        response = await asyncio.to_thread(
            client.images.generate, **kwargs
        )

        mime = "image/png"
        fmt = (request.output_format or "").lower()
        if fmt == "jpeg" or fmt == "jpg":
            mime = "image/jpeg"
        elif fmt == "webp":
            mime = "image/webp"

        images: list[ImageOutput] = []
        for item in (response.data or []):
            b64 = getattr(item, "b64_json", None)
            if b64:
                images.append(ImageOutput(mime_type=mime, data=b64))

        usage_in = 0
        usage_out = 0
        usage = getattr(response, "usage", None)
        if usage is not None:
            usage_in = getattr(usage, "input_tokens", 0) or 0
            usage_out = getattr(usage, "output_tokens", 0) or 0

        return ImageGenerateResponse(
            provider="azure",
            model=request.model,
            images=images,
            usage=Usage(input_tokens=usage_in, output_tokens=usage_out),
        )
