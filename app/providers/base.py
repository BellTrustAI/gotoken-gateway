from typing import Protocol

from app.models import ChatRequest, ChatResponse


class ChatProvider(Protocol):
    async def chat(self, request: ChatRequest) -> ChatResponse: ...

    def list_models(self) -> list[str]: ...
