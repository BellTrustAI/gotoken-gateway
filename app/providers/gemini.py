from app.models import ChatRequest, ChatResponse


class GeminiProvider:
    """Stub — not yet implemented."""

    def list_models(self) -> list[str]:
        return []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        raise NotImplementedError("Gemini provider is not yet implemented")
