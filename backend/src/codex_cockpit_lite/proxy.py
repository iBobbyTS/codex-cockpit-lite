"""API proxy core — reverse proxy to OpenAI API with auto account switching."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import build_auth_headers, build_search_headers
from .config import list_selected_accounts, load_config
from .models import SpeedMode

logger = logging.getLogger(__name__)

UPSTREAM_BASE = "https://api.openai.com"
CHATGPT_BASE = "https://chatgpt.com"
CODEX_UPSTREAM_BASE = f"{CHATGPT_BASE}/backend-api/codex"
UPSTREAM_TIMEOUT = httpx.Timeout(60.0, connect=20.0)

# Request counters
_request_count = 0
_active_account_id = ""
_account_switch_lock = threading.Lock()
_recent_requests: list[dict] = []


def is_account_schedulable(account) -> bool:
    """An account is usable until either the 5h or 7d quota is 100% consumed."""
    return account.quota.hourly_percent > 0 and account.quota.weekly_percent > 0


def _account_payload(account) -> dict:
    return {
        "id": account.id,
        "email": account.email,
        "auth_mode": account.auth_mode.value,
    }


def _next_schedulable_account(accounts, current_id: str):
    if not accounts:
        return None
    start_index = next(
        (index for index, account in enumerate(accounts) if account.id == current_id),
        -1,
    )
    for offset in range(1, len(accounts) + 1):
        account = accounts[(start_index + offset) % len(accounts)]
        if is_account_schedulable(account):
            return account
    return None


def get_active_account(config_dir: Path | None = None) -> dict | None:
    """Return the active schedulable account, advancing if it became exhausted."""
    accounts = list_selected_accounts(config_dir)
    global _active_account_id
    with _account_switch_lock:
        active = next(
            (
                account
                for account in accounts
                if account.id == _active_account_id and is_account_schedulable(account)
            ),
            None,
        )
        if active is None:
            active = _next_schedulable_account(accounts, _active_account_id)
            _active_account_id = active.id if active else ""
        return _account_payload(active) if active else None


def switch_to_next_account(config_dir: Path | None = None) -> dict | None:
    """Switch to the next selected account whose 5h and 7d quotas remain positive."""
    global _active_account_id
    accounts = list_selected_accounts(config_dir)
    with _account_switch_lock:
        meta = _next_schedulable_account(accounts, _active_account_id)
        _active_account_id = meta.id if meta else ""
    if meta is None:
        return None
    active_index = next(index for index, account in enumerate(accounts) if account.id == meta.id)
    logger.info(
        "Switched to account %d/%d: %s (%s)",
        active_index + 1,
        len(accounts),
        meta.email,
        meta.id,
    )
    return _account_payload(meta)


def activate_account(account_id: str, config_dir: Path | None = None) -> dict | None:
    """Force the active cursor to a specific selected, schedulable account."""
    accounts = list_selected_accounts(config_dir)
    account = next(
        (
            candidate
            for candidate in accounts
            if candidate.id == account_id and is_account_schedulable(candidate)
        ),
        None,
    )
    if account is None:
        return None
    global _active_account_id
    with _account_switch_lock:
        _active_account_id = account.id
    logger.info("Activated account: %s (%s)", account.email, account.id)
    return _account_payload(account)


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


def get_active_index(config_dir: Path | None = None) -> int:
    accounts = list_selected_accounts(config_dir)
    active = get_active_account(config_dir)
    if active is None:
        return 0
    return next(
        (index for index, account in enumerate(accounts) if account.id == active["id"]),
        0,
    )


def _build_upstream_headers(
    request_headers: dict[str, str], auth_headers: dict[str, str]
) -> dict[str, str]:
    """Overlay trusted auth without emitting case-variant duplicate headers."""
    headers = {name.lower(): value for name, value in request_headers.items()}
    for name in ("host", "connection", "transfer-encoding", "content-length"):
        headers.pop(name, None)
    headers.update({name.lower(): value for name, value in auth_headers.items()})
    headers.setdefault("originator", "codex-tui")
    return headers


def _build_downstream_headers(upstream_headers: httpx.Headers, *, decoded: bool) -> dict[str, str]:
    """Drop hop-by-hop headers and stale encoding metadata after decoding."""
    excluded = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
    if decoded:
        excluded.update({"content-encoding", "content-length"})
    return {name: value for name, value in upstream_headers.items() if name.lower() not in excluded}


async def _stream_upstream_response(upstream_response: httpx.Response, client: httpx.AsyncClient):
    """Forward undecoded bytes while keeping the upstream connection alive."""
    try:
        async for chunk in upstream_response.aiter_raw():
            yield chunk
    finally:
        await upstream_response.aclose()
        await client.aclose()


async def _read_and_close_upstream(
    upstream_response: httpx.Response, client: httpx.AsyncClient
) -> bytes:
    """Read a streamed response before closing its owning client."""
    try:
        return await upstream_response.aread()
    finally:
        await upstream_response.aclose()
        await client.aclose()


def _with_original_query(request: Request, upstream_url: str) -> str:
    """Preserve client query parameters while keeping the upstream host fixed."""
    query = request.url.query
    if not query:
        return upstream_url
    if isinstance(query, bytes):
        query = query.decode("ascii")
    separator = "&" if "?" in upstream_url else "?"
    return f"{upstream_url}{separator}{query}"


async def proxy_responses(request: Request, config_dir: Path | None = None) -> Response:
    """Proxy /v1/responses with SSE streaming and auto-switch on 429."""
    return await _proxy_with_retry(
        request,
        f"{CODEX_UPSTREAM_BASE}/responses",
        config_dir,
        is_sse=True,
    )


async def proxy_responses_compact(request: Request, config_dir: Path | None = None) -> Response:
    """Proxy /v1/responses/compact."""
    return await _proxy_with_retry(
        request,
        f"{CODEX_UPSTREAM_BASE}/responses/compact",
        config_dir,
        is_sse=True,
    )


async def proxy_chat_completions(request: Request, config_dir: Path | None = None) -> Response:
    """Proxy /v1/chat/completions."""
    return await _proxy_with_retry(
        request,
        f"{UPSTREAM_BASE}/v1/chat/completions",
        config_dir,
        is_sse=True,
    )


async def proxy_models(request: Request, config_dir: Path | None = None) -> Response:
    """Proxy /v1/models."""
    return await _proxy_with_retry(
        request,
        f"{CODEX_UPSTREAM_BASE}/models",
        config_dir,
    )


async def proxy_images_generations(request: Request, config_dir: Path | None = None) -> Response:
    """Proxy /v1/images/generations."""
    return await _proxy_with_retry(
        request,
        f"{CODEX_UPSTREAM_BASE}/images/generations",
        config_dir,
    )


async def proxy_images_edits(request: Request, config_dir: Path | None = None) -> Response:
    """Proxy /v1/images/edits."""
    return await _proxy_with_retry(
        request,
        f"{CODEX_UPSTREAM_BASE}/images/edits",
        config_dir,
    )


async def proxy_alpha_search(request: Request, config_dir: Path | None = None) -> Response:
    """Proxy /v1/alpha/search to ChatGPT backend (OAuth only)."""
    upstream = f"{CHATGPT_BASE}/backend-api/codex/alpha/search"

    account = get_active_account(config_dir)
    if not account:
        return JSONResponse(
            {"error": "No active account configured"},
            status_code=503,
        )

    try:
        headers = build_search_headers(account["id"], config_dir)
    except (OSError, ValueError) as error:
        return JSONResponse({"error": str(error)}, status_code=500)

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

        record_request(
            {
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "account_id": account["id"],
                "account_email": account["email"],
                "method": "POST",
                "path": "/v1/alpha/search",
                "model": "",
                "status": resp.status_code,
                "duration_ms": duration_ms,
            }
        )

        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=_build_downstream_headers(resp.headers, decoded=True),
        )
    except httpx.HTTPError as error:
        duration_ms = int((time.time() - start) * 1000)
        record_request(
            {
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "account_id": account["id"],
                "account_email": account["email"],
                "method": "POST",
                "path": "/v1/alpha/search",
                "model": "",
                "status": 502,
                "duration_ms": duration_ms,
                "error": str(error),
            }
        )
        return JSONResponse({"error": f"Upstream error: {error}"}, status_code=502)


async def _proxy_with_retry(
    request: Request,
    upstream_url: str,
    config_dir: Path | None = None,
    is_sse: bool = False,
    max_retries: int = 8,
) -> Response:
    """Proxy a request to upstream, retrying with next account on 429 or rate-limit."""
    cfg = load_config(config_dir)
    auto_switch = cfg.api.auto_switch
    upstream_url = _with_original_query(request, upstream_url)

    for attempt in range(max_retries + 1):
        account = get_active_account(config_dir)
        if not account:
            return JSONResponse(
                {"error": "No active account configured"},
                status_code=503,
            )

        try:
            headers = build_auth_headers(account["id"], config_dir)
        except (OSError, ValueError) as error:
            logger.warning("Auth header build failed for %s: %s", account["id"], error)
            if auto_switch.enabled and attempt < max_retries:
                switch_to_next_account(config_dir)
                continue
            return JSONResponse({"error": str(error)}, status_code=500)

        # Add service_tier header based on speed config
        if cfg.api.speed == SpeedMode.FAST:
            headers["service_tier"] = "priority"

        # Forward request
        body = await request.body()
        req_headers = _build_upstream_headers(dict(request.headers), headers)

        start = time.time()
        client: httpx.AsyncClient | None = None
        try:
            client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
            if is_sse:
                upstream_req = client.build_request(
                    method=request.method,
                    url=upstream_url,
                    content=body,
                    headers=req_headers,
                )
                upstream_resp = await client.send(upstream_req, stream=True)
            else:
                upstream_resp = await client.request(
                    method=request.method,
                    url=upstream_url,
                    content=body,
                    headers=req_headers,
                )

            duration_ms = int((time.time() - start) * 1000)

            # Log the request
            record_request(
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": time.time(),
                    "account_id": account["id"],
                    "account_email": account["email"],
                    "method": request.method,
                    "path": request.url.path,
                    "model": _extract_model(body),
                    "status": upstream_resp.status_code,
                    "duration_ms": duration_ms,
                }
            )

            # Check for rate limiting
            if upstream_resp.status_code == 429:
                logger.warning(
                    "Rate limited on account %s (attempt %d), switching...",
                    account["email"],
                    attempt + 1,
                )
                if auto_switch.enabled and attempt < max_retries:
                    if is_sse:
                        await _read_and_close_upstream(upstream_resp, client)
                    else:
                        await client.aclose()
                    switch_to_next_account(config_dir)
                    continue
                content = (
                    await _read_and_close_upstream(upstream_resp, client)
                    if is_sse
                    else upstream_resp.content
                )
                if not is_sse:
                    await client.aclose()
                return Response(
                    content=content,
                    status_code=429,
                    headers=_build_downstream_headers(upstream_resp.headers, decoded=True),
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
                        if is_sse:
                            await _read_and_close_upstream(upstream_resp, client)
                        else:
                            await client.aclose()
                        switch_to_next_account(config_dir)
                        continue
                except ValueError:
                    logger.debug("Ignoring invalid x-ratelimit-remaining-tokens header")

            if is_sse:
                return StreamingResponse(
                    _stream_upstream_response(upstream_resp, client),
                    status_code=upstream_resp.status_code,
                    headers=_build_downstream_headers(upstream_resp.headers, decoded=False),
                    media_type="text/event-stream",
                )
            else:
                content = upstream_resp.content
                await client.aclose()
                return Response(
                    content=content,
                    status_code=upstream_resp.status_code,
                    headers=_build_downstream_headers(upstream_resp.headers, decoded=True),
                )

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if client is not None:
                await client.aclose()
            duration_ms = int((time.time() - start) * 1000)
            record_request(
                {
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
                }
            )
            if auto_switch.enabled and attempt < max_retries:
                switch_to_next_account(config_dir)
                continue
            return JSONResponse({"error": f"Upstream error: {e}"}, status_code=502)

    return JSONResponse(
        {"error": "All accounts exhausted"},
        status_code=429,
    )


def _extract_model(body: bytes) -> str:
    try:
        data = json.loads(body)
        return data.get("model", "")
    except json.JSONDecodeError, UnicodeDecodeError, AttributeError:
        return ""
