from fastapi import APIRouter, Depends, HTTPException

from app.auth import verify_api_key
from app.models import ChatRequest, ChatResponse, HealthResponse, ModelsResponse
from app.providers.bedrock import BedrockProvider
from app.providers.openai import OpenAIProvider
from app.providers.gemini import GeminiProvider

router = APIRouter()

_providers = {
    "bedrock": BedrockProvider(),
    "openai": OpenAIProvider(),
    "gemini": GeminiProvider(),
}


def _get_provider(provider_name: str):
    if provider_name not in _providers:
        raise HTTPException(status_code=400, detail=f"Unknown provider '{provider_name}'. Available: {list(_providers.keys())}")
    return _providers[provider_name]


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _: str = Depends(verify_api_key)) -> ChatResponse:
    provider = _get_provider(request.provider)
    try:
        return await provider.chat(request)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Provider error: {e}")


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/models", response_model=ModelsResponse)
async def list_models(provider: str = "bedrock", _: str = Depends(verify_api_key)) -> ModelsResponse:
    p = _get_provider(provider)
    return ModelsResponse(provider=provider, models=p.list_models())
