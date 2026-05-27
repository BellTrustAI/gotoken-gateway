from pydantic import BaseModel


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    provider: str
    model: str
    messages: list[Message]
    max_tokens: int = 512
    max_completion_tokens: int = 0
    temperature: float = 0.7
    reasoning_effort: str = ""
    stream: bool = False


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class ChatResponse(BaseModel):
    provider: str
    model: str
    content: str
    usage: Usage | None = None


class ModelsResponse(BaseModel):
    provider: str
    models: list[str]


class HealthResponse(BaseModel):
    status: str = "ok"


class ErrorResponse(BaseModel):
    error: str
