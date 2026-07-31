"""Inbound API-key authentication for externally served model endpoints."""

from __future__ import annotations

import hmac

from fastapi import Request

_BACKEND_SEARCH_PATH = "/backend-api/codex/alpha/search"
_STATUS_PATH = "/v1/cockpit/status"


def requires_api_key(request: Request) -> bool:
    """Return whether this request targets an externally served API endpoint."""
    if request.method.upper() == "OPTIONS":
        return False
    path = request.url.path.rstrip("/") or "/"
    if path == _STATUS_PATH:
        return False
    return path.startswith("/v1/") or path == _BACKEND_SEARCH_PATH


def has_valid_api_key(request: Request, configured_api_key: str) -> bool:
    """Validate the original protocol's X-API-Key or Bearer credential fields."""
    if not configured_api_key:
        return False

    x_api_key = request.headers.get("x-api-key")
    if x_api_key is not None and hmac.compare_digest(x_api_key.strip(), configured_api_key):
        return True

    authorization = request.headers.get("authorization")
    if authorization is None:
        return False
    scheme, separator, credential = authorization.partition(" ")
    return (
        bool(separator)
        and scheme.lower() == "bearer"
        and hmac.compare_digest(credential.strip(), configured_api_key)
    )
