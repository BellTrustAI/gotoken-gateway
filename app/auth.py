from fastapi import Depends, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.provider_config import get_config

security = HTTPBearer()


def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security)) -> str:
    key = credentials.credentials
    if key == settings.gateway_api_key:
        return key
    config = get_config()
    for t in config.api_tokens:
        if t.token == key:
            return key
    raise HTTPException(status_code=401, detail="Invalid API key")
