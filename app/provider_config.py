"""Provider credential + model config persistence.

Reads from config.json at repo root; falls back to env vars for
AWS credentials if config.json has no values. Admin API writes back
to config.json.
"""

import json
import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

CONFIG_PATH = Path(os.environ.get("GATEWAY_CONFIG_PATH", Path(__file__).resolve().parent.parent / "config.json"))


# ---- Provider config models ----

class BedrockConfig(BaseModel):
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-west-2"
    models: dict[str, str] = {}  # model_id -> inference-profile-arn


class OpenAIConfig(BaseModel):
    api_key: str = ""
    base_url: str = ""
    models: list[str] = []


class GeminiConfig(BaseModel):
    api_key: str = ""
    models: list[str] = []


class AzureConfig(BaseModel):
    api_key: str = ""
    endpoint: str = ""
    api_version: str = "2025-01-01-preview"
    models: list[str] = []
    use_entra_id: bool = False
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: str = ""


class ApiToken(BaseModel):
    name: str
    token: str
    created_at: str = ""


class GatewayConfig(BaseModel):
    bedrock: BedrockConfig = BedrockConfig()
    openai: OpenAIConfig = OpenAIConfig()
    gemini: GeminiConfig = GeminiConfig()
    azure: AzureConfig = AzureConfig()
    api_tokens: list[ApiToken] = Field(default_factory=list)


# ---- Load / Save ----

def _load_raw() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def load_config() -> GatewayConfig:
    raw = _load_raw()
    # Merge env vars as fallback for Bedrock AWS creds
    bedrock_raw = raw.get("bedrock", {})
    if not bedrock_raw.get("aws_access_key_id"):
        bedrock_raw["aws_access_key_id"] = os.environ.get("AWS_ACCESS_KEY_ID", "")
    if not bedrock_raw.get("aws_secret_access_key"):
        bedrock_raw["aws_secret_access_key"] = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    if not bedrock_raw.get("aws_region"):
        bedrock_raw["aws_region"] = os.environ.get("AWS_REGION", "us-west-2")
    raw["bedrock"] = bedrock_raw

    openai_raw = raw.get("openai", {})
    if not openai_raw.get("api_key"):
        openai_raw["api_key"] = os.environ.get("OPENAI_API_KEY", "")
    raw["openai"] = openai_raw

    gemini_raw = raw.get("gemini", {})
    if not gemini_raw.get("api_key"):
        gemini_raw["api_key"] = os.environ.get("GEMINI_API_KEY", "")
    raw["gemini"] = gemini_raw

    azure_raw = raw.get("azure", {})
    if not azure_raw.get("api_key"):
        azure_raw["api_key"] = os.environ.get("AZURE_OPENAI_API_KEY", "")
    if not azure_raw.get("endpoint"):
        azure_raw["endpoint"] = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    raw["azure"] = azure_raw

    return GatewayConfig(**raw)


def save_config(config: GatewayConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config.model_dump(), indent=2, ensure_ascii=False))


# Singleton loaded at startup
_config: Optional[GatewayConfig] = None


def get_config() -> GatewayConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> GatewayConfig:
    global _config
    _config = load_config()
    return _config
