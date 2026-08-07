"""API-key authentication for write endpoints."""

import secrets
from typing import Annotated

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from api.core.config import settings

API_KEY_NAME = "X-API-KEY"

# auto_error=False so a *missing* header produces the same 401 as a *wrong*
# one; the default would raise a 403 and leak which of the two happened.
_api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def validate_api_key(api_key: Annotated[str | None, Security(_api_key_header)]) -> str:
    """Constant-time comparison — a plain `!=` leaks key material through
    response timing, one byte at a time."""
    if api_key is None or not secrets.compare_digest(
        api_key.encode("utf-8"), settings.api_master_key.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return api_key
