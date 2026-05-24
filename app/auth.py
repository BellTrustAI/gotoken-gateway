from typing import Optional

from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.provider_config import get_config

security = HTTPBearer(auto_error=False)


def _validate_key(key: str) -> str:
    if key == settings.gateway_api_key:
        return key
    config = get_config()
    for t in config.api_tokens:
        if t.token == key:
            return key
    raise HTTPException(status_code=401, detail="Invalid API key")


def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
) -> str:
    if credentials:
        return _validate_key(credentials.credentials)
    if x_api_key:
        return _validate_key(x_api_key)
    raise HTTPException(status_code=401, detail="Missing API key")
