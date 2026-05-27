import base64
import json
import logging
import re

from google import genai
from google.genai import types
from google.oauth2 import service_account

from app.models import ChatRequest, ChatResponse, ContentPart, ImageOutput, Usage
from app.provider_config import get_config

logger = logging.getLogger(__name__)

_DATA_URL_RE = re.compile(r"data:([^;]+);base64,(.+)")


def _content_to_str(content) -> str:
    """Extract text from Message.content, whether str or list."""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        part_type = item.type if hasattr(item, "type") else item.get("type", "")
        if part_type == "text":
            parts.append(item.text if hasattr(item, "text") else item.get("text", ""))
    return "\n".join(parts)


def _content_to_parts(content) -> list:
    """Convert Message.content (str or list[ContentPart]) to Gemini Part list."""
    if isinstance(content, str):
        return [types.Part(text=content)]

    parts = []
    for item in content:
        part_type = item.type if hasattr(item, "type") else item.get("type", "")
        if part_type == "text":
            text = item.text if hasattr(item, "text") else item.get("text", "")
            parts.append(types.Part(text=text))
        elif part_type == "image_url":
            url = ""
            if hasattr(item, "image_url") and item.image_url:
                url = item.image_url.url
            elif isinstance(item, dict):
                url = item.get("image_url", {}).get("url", "")
            if url.startswith("data:"):
                m = _DATA_URL_RE.match(url)
                if m:
                    mime_type = m.group(1)
                    data = base64.b64decode(m.group(2))
                    parts.append(types.Part(
                        inline_data=types.Blob(data=data, mime_type=mime_type)
                    ))
    return parts


class GeminiProvider:
    def __init__(self):
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        config = get_config().gemini
        if not config.project_id:
            raise ValueError("Gemini project_id is not configured")
        if not config.credentials_json:
            raise ValueError("Gemini credentials_json is not configured")

        credentials_info = json.loads(config.credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        self._client = genai.Client(
            vertexai=True,
            project=config.project_id,
            location=config.location,
            credentials=credentials,
        )
        return self._client

    def list_models(self) -> list[str]:
        config = get_config().gemini
        return [m.strip() for m in config.models if m.strip()]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        client = self._ensure_client()
        config = get_config().gemini

        if not config.models:
            raise ValueError("No Gemini models configured")

        # Build contents and extract system instruction
        contents = []
        system_instruction = None

        for msg in request.messages:
            if msg.role == "system":
                system_instruction = _content_to_str(msg.content)
            elif msg.role == "user":
                parts = _content_to_parts(msg.content)
                if parts:
                    contents.append(types.Content(role="user", parts=parts))
            elif msg.role == "assistant":
                parts = _content_to_parts(msg.content)
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

        if not contents:
            raise ValueError("No user or assistant messages found")

        generate_config = types.GenerateContentConfig(
            max_output_tokens=request.max_completion_tokens or request.max_tokens or 512,
            temperature=request.temperature,
        )

        # Enable image output for image-generation models
        if "image" in request.model.lower():
            generate_config.response_modalities = ["TEXT", "IMAGE"]

        if system_instruction:
            generate_config.system_instruction = system_instruction

        try:
            response = client.models.generate_content(
                model=request.model,
                contents=contents,
                config=generate_config,
            )
        except Exception as e:
            logger.exception("Gemini Vertex AI API error for model=%s", request.model)
            raise ValueError(f"Gemini API error: {e}")

        text_parts = []
        images = []
        # Iterate all candidates and parts to capture text/images
        for candidate in (response.candidates or []):
            if not candidate.content or not candidate.content.parts:
                continue
            for part in candidate.content.parts:
                if part.text:
                    text_parts.append(part.text)
                if hasattr(part, "inline_data") and part.inline_data:
                    images.append(ImageOutput(
                        mime_type=part.inline_data.mime_type,
                        data=base64.b64encode(part.inline_data.data).decode(),
                    ))
                # google-genai SDK may put thinking in part.thought (bool + text)
                if hasattr(part, "thought") and part.thought:
                    continue  # skip thought-only parts, they are internal reasoning

        content = "\n".join(text_parts) if text_parts else (response.text or "")

        usage = None
        if response.usage_metadata:
            usage = Usage(
                input_tokens=response.usage_metadata.prompt_token_count or 0,
                output_tokens=response.usage_metadata.candidates_token_count or 0,
            )

        return ChatResponse(
            provider="gemini",
            model=request.model,
            content=content,
            images=images if images else None,
            usage=usage,
        )
