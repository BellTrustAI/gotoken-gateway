from app.models import ChatRequest, ChatResponse


class OpenAIProvider:
    """Stub — not yet implemented."""

    def list_models(self) -> list[str]:
        return []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise NotImplementedError("OpenAI provider is not yet implemented")
