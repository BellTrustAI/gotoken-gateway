import asyncio
import logging

from openai import AzureOpenAI

from app.models import ChatRequest, ChatResponse, Usage
from app.provider_config import get_config

logger = logging.getLogger(__name__)


class AzureFoundryProvider:
    def __init__(self) -> None:
        cfg = get_config().azure
        self._client = AzureOpenAI(
            api_key=cfg.api_key,
            api_version=cfg.api_version,
            azure_endpoint=cfg.endpoint,
        )
        self._models = cfg.models

    def list_models(self) -> list[str]:
        return list(self._models)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        if request.model not in self._models:
            available = ", ".join(self._models)
            raise ValueError(f"Unknown Azure model '{request.model}'. Available: {available}")

        messages = [{"role": m.role, "content": m.content} for m in request.messages]

        is_codex = "codex" in request.model.lower()

        kwargs: dict = {
            "model": request.model,
            "messages": messages,
        }

        if is_codex:
            # reasoning models use max_completion_tokens, no temperature support
            kwargs["max_completion_tokens"] = request.max_completion_tokens or request.max_tokens
            if request.reasoning_effort:
                kwargs["reasoning_effort"] = request.reasoning_effort
        else:
            kwargs["max_tokens"] = request.max_tokens
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
