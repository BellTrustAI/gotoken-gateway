import asyncio
import logging

from azure.identity import ClientSecretCredential, get_bearer_token_provider
from openai import OpenAI

from app.models import ChatRequest, ChatResponse, Usage
from app.provider_config import get_config

logger = logging.getLogger(__name__)

_AZURE_AD_SCOPE = "https://ai.azure.com/.default"

# Models that need Entra ID or special auth — API Key returns "unsupported operation"
_ENTRA_REQUIRED_MODELS = {"gpt-5.4-pro", "gpt-5.2-codex", "gpt-5.3-codex"}


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

    def _pick_client(self, model: str) -> OpenAI:
        if model in _ENTRA_REQUIRED_MODELS:
            if self._entra_client is None:
                raise ValueError(
                    f"Model '{model}' requires Entra ID authentication. "
                    "Please configure Entra ID in Azure AI settings."
                )
            return self._entra_client
        return self._client

    def list_models(self) -> list[str]:
        self._ensure_clients()
        return list(self._models)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self._ensure_clients()
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
            if request.reasoning_effort:
                kwargs["reasoning_effort"] = request.reasoning_effort
        else:
            if request.temperature > 0:
                kwargs["temperature"] = request.temperature

        client = self._pick_client(request.model)
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
