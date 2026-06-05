import asyncio
import json
import logging

import boto3

from app.models import ChatRequest, ChatResponse, Usage
from app.provider_config import get_config

logger = logging.getLogger(__name__)


class BedrockProvider:
    def __init__(self) -> None:
        cfg = get_config().bedrock
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=cfg.aws_region,
            aws_access_key_id=cfg.aws_access_key_id,
            aws_secret_access_key=cfg.aws_secret_access_key,
        )
        self._models = cfg.models

    def list_models(self) -> list[str]:
        return list(self._models.keys())

    async def chat(self, request: ChatRequest) -> ChatResponse:
        model_id = request.model
        if model_id not in self._models:
            available = ", ".join(self._models.keys())
            raise ValueError(f"Unknown Bedrock model '{model_id}'. Available: {available}")

        inference_profile_arn = self._models[model_id]

        system_messages = [m for m in request.messages if m.role == "system"]
        chat_messages = [m for m in request.messages if m.role != "system"]

        body: dict = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": request.max_tokens,
            "messages": [m.model_dump() for m in chat_messages],
        }

        if system_messages:
            body["system"] = [{"type": "text", "text": m.content} for m in system_messages]

        if request.temperature > 0:
            body["temperature"] = request.temperature

        response = await asyncio.to_thread(
            self._client.invoke_model,
            modelId=inference_profile_arn,
            body=json.dumps(body),
        )

        response_body = json.loads(response["body"].read())

        content_blocks = response_body.get("content", [])
        text = "".join(block.get("text", "") for block in content_blocks if block.get("type") == "text")

        usage_data = response_body.get("usage", {}) or {}
        usage = Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
        )

        return ChatResponse(
            provider="bedrock",
            model=request.model,
            content=text,
            usage=usage,
            raw_usage=usage_data or None,
        )
