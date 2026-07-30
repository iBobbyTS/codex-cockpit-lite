"""API proxy core — reverse proxy to OpenAI API with auto account switching."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse, JSONResponse

from auth import build_auth_headers, build_search_headers, get_auth_mode, parse_auth_file
from config import load_auth_file, load_config, list_selected_accounts, load_meta
from models import AuthMode, SpeedMode

logger = logging.getLogger(__name__)

UPSTREAM_BASE = "https://api.openai.com"
CHATGPT_BASE = "https://chatgpt.com"
UPSTREAM_TIMEOUT = httpx.Timeout(60.0, connect=20.0)

# Rate-limit tracking per account
_rate_limit_cache: dict[str, dict] = {}
_account_switch_lock = asyncio.Lock()

# Request counters
_request_count = 0
_active_account_index = 0
_recent_requests: list[dict] = []


def get_active_account(config_dir: Optional[Path] = None) -> Optional[dict]:
    """Get the currently active account (first selected, enabled)."""
    accounts = list_selected_accounts(config_dir)
    if not accounts:
        return None
    global _active_account_index
    if _active_account_index >= len(accounts):
        _active_account_index = 0
    meta = accounts[_active_account_index]
    return {
        "id": meta.id,
        "email": meta.email,
        "auth_mode": meta.auth_mode.value,
    }


def switch_to_next_account(config_dir: Optional[Path] = None) -> Optional[dict]:
    """Switch to the next available selected account."""
    global _active_account_index
    accounts = list_selected_accounts(config_dir)
    if not accounts:
        return None
    _active_account_index = (_active_account_index + 1) % len(accounts)
    meta = accounts[_active_account_index]
    logger.info(
        "Switched to account %d/%d: %s (%s)",
        _active_account_index + 1, len(accounts), meta.email, meta.id,
    )
    return {
        "id": meta.id,
        "email": meta.email,
        "auth_mode": meta.auth_mode.value,
    }


def record_request(log_entry: dict) -> None:
    global _request_count, _recent_requests
    _request_count += 1
    _recent_requests.append(log_entry)
    # Keep last 100
    if len(_recent_requests) > 100:
        _recent_requests = _recent_requests[-100:]


def get_recent_requests() -> list[dict]:
    return list(_recent_requests)


def get_request_count() -> int:
    return _request_count


def get_active_index() -> int:
    return _active_account_index


async def proxy_responses(request: Request, config_dir: Optional[Path] = None) -> Response:
    """Proxy /v1/responses with SSE streaming and auto-switch on 429."""
    return await _proxy_with_retry(
        request, f"{UPSTREAM_BASE}/v1/responses", config_dir,
        is_sse=True,
    )


async def proxy_responses_compact(request: Request, config_dir: Optional[Path] = None) -> Response:
    """Proxy /v1/responses/compact."""
    return await _proxy_with_retry(
        request, f"{UPSTREAM_BASE}/v1/responses/compact", config_dir,
        is_sse=True,
    )


async def proxy_chat_completions(request: Request, config_dir: Optional[Path] = None) -> Response:
    """Proxy /v1/chat/completions."""
    return await _proxy_with_retry(
        request, f"{UPSTREAM_BASE}/v1/chat/completions", config_dir,
        is_sse=True,
    )


async def proxy_models(request: Request, config_dir: Optional[Path] = None) -> Response:
    """Proxy /v1/models."""
    return await _proxy_with_retry(
        request, f"{UPSTREAM_BASE}/v1/models", config_dir,
    )


async def proxy_images_generations(request: Request, config_dir: Optional[Path] = None) -> Response:
    """Proxy /v1/images/generations."""
    return await _proxy_with_retry(
        request, f"{UPSTREAM_BASE}/v1/images/generations", config_dir,
    )


async def proxy_images_edits(request: Request, config_dir: Optional[Path] = None) -> Response:
    """Proxy /v1/images/edits."""
    return await _proxy_with_retry(
        request, f"{UPSTREAM_BASE}/v1/images/edits", config_dir,
    )


async def proxy_alpha_search(request: Request, config_dir: Optional[Path] = None) -> Response:
    """Proxy /v1/alpha/search to ChatGPT backend (OAuth only)."""
    upstream = f"{CHATGPT_BASE}/backend-api/codex/alpha/search"

    account = get_active_account(config_dir)
    if not account:
        return JSONResponse(
            {"error": "No active account configured"}, status_code=503,
        )

    try:
        headers = build_search_headers(account["id"], config_dir)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    body = await request.body()
    start = time.time()

    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            resp = await client.post(
                upstream,
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
        duration_ms = int((time.time() - start) * 1000)

        record_request({
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "account_id": account["id"],
            "account_email": account["email"],
            "method": "POST",
            "path": "/v1/alpha/search",
            "model": "",
            "status": resp.status_code,
            "duration_ms": duration_ms,
        })

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)
        record_request({
            "id": str(uuid.uuid4()),
            "timestamp": time.time(),
            "account_id": account["id"],
            "account_email": account["email"],
            "method": "POST",
            "path": "/v1/alpha/search",
            "model": "",
            "status": 502,
            "duration_ms": duration_ms,
            "error": str(e),
        })
        return JSONResponse({"error": f"Upstream error: {e}"}, status_code=502)


async def _proxy_with_retry(
    request: Request,
    upstream_url: str,
    config_dir: Optional[Path] = None,
    is_sse: bool = False,
    max_retries: int = 8,
) -> Response:
    """Proxy a request to upstream, retrying with next account on 429 or rate-limit."""
    cfg = load_config(config_dir)
    auto_switch = cfg.api.auto_switch

    for attempt in range(max_retries + 1):
        account = get_active_account(config_dir)
        if not account:
            return JSONResponse(
                {"error": "No active account configured"}, status_code=503,
            )

        try:
            headers = build_auth_headers(account["id"], config_dir)
        except Exception as e:
            logger.warning("Auth header build failed for %s: %s", account["id"], e)
            if auto_switch.enabled and attempt < max_retries:
                switch_to_next_account(config_dir)
                continue
            return JSONResponse({"error": str(e)}, status_code=500)

        # Add service_tier header based on speed config
        raw_auth = load_auth_file(account["id"], config_dir)
        auth = parse_auth_file(raw_auth)
        mode = get_auth_mode(auth)
        if mode == AuthMode.OAUTH and cfg.api.speed == SpeedMode.FAST:
            headers["service_tier"] = "priority"

        # Forward request
        body = await request.body()
        req_headers = dict(request.headers)
        # Remove hop-by-hop headers
        for h in ["host", "connection", "transfer-encoding", "content-length"]:
            req_headers.pop(h, None)
        # Override with our auth headers
        req_headers.update(headers)

        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
                if is_sse:
                    # For SSE, stream the response
                    upstream_req = client.build_request(
                        method=request.method,
                        url=upstream_url,
                        content=body,
                        headers=req_headers,
                    )
                    upstream_resp = await client.send(
                        upstream_req, stream=True,
                    )
                else:
                    upstream_resp = await client.request(
                        method=request.method,
                        url=upstream_url,
                        content=body,
                        headers=req_headers,
                    )

            duration_ms = int((time.time() - start) * 1000)

            # Log the request
            record_request({
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "account_id": account["id"],
                "account_email": account["email"],
                "method": request.method,
                "path": request.url.path,
                "model": _extract_model(body),
                "status": upstream_resp.status_code,
                "duration_ms": duration_ms,
            })

            # Check for rate limiting
            if upstream_resp.status_code == 429:
                logger.warning(
                    "Rate limited on account %s (attempt %d), switching...",
                    account["email"], attempt + 1,
                )
                if auto_switch.enabled and attempt < max_retries:
                    switch_to_next_account(config_dir)
                    continue
                return Response(
                    content=upstream_resp.content,
                    status_code=429,
                    headers=dict(upstream_resp.headers),
                )

            # Check rate-limit headers for proactive switching
            remaining = upstream_resp.headers.get("x-ratelimit-remaining-tokens")
            if remaining is not None:
                try:
                    rem = int(remaining)
                    if rem <= 0 and auto_switch.enabled and attempt < max_retries:
                        logger.info(
                            "Account %s tokens exhausted, switching...",
                            account["email"],
                        )
                        switch_to_next_account(config_dir)
                        continue
                except ValueError:
                    pass

            if is_sse:
                return StreamingResponse(
                    upstream_resp.aiter_bytes(),
                    status_code=upstream_resp.status_code,
                    headers=dict(upstream_resp.headers),
                    media_type="text/event-stream",
                )
            else:
                return Response(
                    content=upstream_resp.content,
                    status_code=upstream_resp.status_code,
                    headers=dict(upstream_resp.headers),
                )

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            duration_ms = int((time.time() - start) * 1000)
            record_request({
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "account_id": account["id"],
                "account_email": account["email"],
                "method": request.method,
                "path": request.url.path,
                "model": _extract_model(body),
                "status": 502,
                "duration_ms": duration_ms,
                "error": str(e),
            })
            if auto_switch.enabled and attempt < max_retries:
                switch_to_next_account(config_dir)
                continue
            return JSONResponse({"error": f"Upstream error: {e}"}, status_code=502)

    return JSONResponse(
        {"error": "All accounts exhausted"}, status_code=429,
    )


def _extract_model(body: bytes) -> str:
    try:
        data = json.loads(body)
        return data.get("model", "")
    except Exception:
        return ""
