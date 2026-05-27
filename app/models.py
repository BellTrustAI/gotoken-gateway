from typing import Union

from pydantic import BaseModel


class ImageUrl(BaseModel):
    url: str


class ContentPart(BaseModel):
    type: str
    text: str = ""
    image_url: ImageUrl | None = None


class Message(BaseModel):
    role: str
    content: Union[str, list[ContentPart]]


class ResponseRequest(BaseModel):
    provider: str
    model: str
    input: str = ""
    max_output_tokens: int = 512
    reasoning_effort: str = ""


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


class ImageOutput(BaseModel):
    mime_type: str
    data: str  # base64 encoded bytes


class ChatResponse(BaseModel):
    provider: str
    model: str
    content: str
    images: list[ImageOutput] | None = None
    usage: Usage | None = None


class ModelsResponse(BaseModel):
    provider: str
    models: list[str]


class HealthResponse(BaseModel):
    status: str = "ok"


class ErrorResponse(BaseModel):
    error: str
