import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import verify_api_key
from app.provider_config import (
    ApiToken,
    BedrockConfig,
    GatewayConfig,
    GeminiConfig,
    OpenAIConfig,
    get_config,
    reload_config,
    save_config,
)

admin = APIRouter(prefix="/admin", tags=["admin"])


@admin.get("/providers")
def list_providers(_: str = Depends(verify_api_key)) -> GatewayConfig:
    return get_config()


@admin.put("/providers/bedrock")
def update_bedrock(cfg: BedrockConfig, _: str = Depends(verify_api_key)) -> dict:
    config = get_config()
    config.bedrock = cfg
    save_config(config)
    reload_config()
    return {"status": "ok"}


@admin.put("/providers/openai")
def update_openai(cfg: OpenAIConfig, _: str = Depends(verify_api_key)) -> dict:
    config = get_config()
    config.openai = cfg
    save_config(config)
    reload_config()
    return {"status": "ok"}


@admin.put("/providers/gemini")
def update_gemini(cfg: GeminiConfig, _: str = Depends(verify_api_key)) -> dict:
    config = get_config()
    config.gemini = cfg
    save_config(config)
    reload_config()
    return {"status": "ok"}


class CreateTokenRequest(BaseModel):
    name: str


@admin.get("/tokens")
def list_tokens(_: str = Depends(verify_api_key)) -> list[dict]:
    config = get_config()
    return [
        {"name": t.name, "prefix": t.token[:8] + "..." + t.token[-4:], "created_at": t.created_at}
        for t in config.api_tokens
    ]


@admin.post("/tokens")
def create_token(req: CreateTokenRequest, _: str = Depends(verify_api_key)) -> dict:
    config = get_config()
    for t in config.api_tokens:
        if t.name == req.name:
            raise HTTPException(status_code=409, detail="Token name already exists")
    token = "gt-" + secrets.token_hex(24)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    config.api_tokens.append(ApiToken(name=req.name, token=token, created_at=now))
    save_config(config)
    reload_config()
    return {"name": req.name, "token": token, "created_at": now}


@admin.delete("/tokens/{name}")
def delete_token(name: str, _: str = Depends(verify_api_key)) -> dict:
    config = get_config()
    for i, t in enumerate(config.api_tokens):
        if t.name == name:
            config.api_tokens.pop(i)
            save_config(config)
            reload_config()
            return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Token not found")
