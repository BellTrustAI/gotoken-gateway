import asyncio
import logging

from azure.identity import ClientSecretCredential, get_bearer_token_provider
from openai import OpenAI

from app.models import ChatRequest, ChatResponse, Usage
from app.provider_config import get_config

logger = logging.getLogger(__name__)

_AZURE_AD_SCOPE = "https://cognitiveservices.azure.com/.default"


class AzureFoundryProvider:
    def __init__(self) -> None:
        self._client = None
        self._models: list[str] = []

    def _ensure_client(self):
        cfg = get_config().azure
        self._models = [m.strip() for m in cfg.models if m.strip()]

        if cfg.use_entra_id and cfg.entra_tenant_id:
            credential = ClientSecretCredential(
                tenant_id=cfg.entra_tenant_id,
                client_id=cfg.entra_client_id,
                client_secret=cfg.entra_client_secret,
            )
            token_provider = get_bearer_token_provider(
                credential, _AZURE_AD_SCOPE
            )
            self._client = OpenAI(
                api_key=token_provider,
                base_url=cfg.endpoint,
            )
        else:
            self._client = OpenAI(
                api_key=cfg.api_key,
                base_url=cfg.endpoint,
            )

    def list_models(self) -> list[str]:
        self._ensure_client()
        return list(self._models)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self._ensure_client()
        if request.model not in self._models:
            available = ", ".join(self._models)
            raise ValueError(f"Unknown Azure model '{request.model}'. Available: {available}")

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        is_codex = "codex" in request.model.lower()

        kwargs: dict = {
            "model": request.model,
            "messages": messages,
            "max_completion_tokens": request.max_completion_tokens or request.max_tokens,
        }

        if is_codex:
            # reasoning models: no temperature, support reasoning_effort
            if request.reasoning_effort:
                kwargs["reasoning_effort"] = request.reasoning_effort
        else:
            if request.temperature > 0:
                kwargs["temperature"] = request.temperature

        response = await asyncio.to_thread(
            self._client.chat.completions.create, **kwargs
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
