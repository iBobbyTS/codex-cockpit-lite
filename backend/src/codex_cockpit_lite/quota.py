"""Quota and subscription information fetcher."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path

import httpx

from .account_state import clear_quota, mark_requires_reauth
from .auth import (
    OAuthReauthRequiredError,
    OAuthRefreshError,
    build_auth_headers,
    extract_account_id_from_access_token,
    force_refresh_oauth_token,
    parse_auth_file,
    validate_chatgpt_auth,
)
from .config import load_auth_file, load_meta, save_meta
from .models import AccountMeta, QuotaSnapshot

logger = logging.getLogger(__name__)

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ACCOUNTS_CHECK_URL = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
SUBSCRIPTIONS_URL = "https://chatgpt.com/backend-api/subscriptions"
QUOTA_REFRESH_INTERVAL_SECONDS = 300
REQUEST_TIMEOUT = 30.0


async def refresh_quota(account_id: str, config_dir: Path | None = None) -> QuotaSnapshot:
    """Fetch quota from wham/usage endpoint."""
    try:
        data = await _get_json_with_oauth_retry(
            USAGE_URL,
            account_id,
            config_dir,
            _build_quota_headers,
        )
    except (OSError, ValueError, httpx.HTTPError, OAuthRefreshError) as error:
        logger.warning("Quota fetch failed for %s: %s", account_id, error)
        if isinstance(error, OAuthReauthRequiredError):
            mark_requires_reauth(account_id, str(error), config_dir)
        else:
            clear_quota(account_id, config_dir)
        return QuotaSnapshot()

    rate_limit = data.get("rate_limit", {}) or {}
    primary = rate_limit.get("primary_window", {}) or {}
    secondary = rate_limit.get("secondary_window", {}) or {}

    def _pct(window: dict) -> int:
        used = window.get("used_percent", 0) or 0
        return max(0, min(100, 100 - int(used)))

    def _reset_at(window: dict) -> int | None:
        if window.get("reset_at"):
            return int(window["reset_at"])
        after = window.get("reset_after_seconds", 0)
        if after and after > 0:
            return int(time.time() + after)
        return None

    snapshot = QuotaSnapshot(
        hourly_percent=_pct(primary),
        weekly_percent=_pct(secondary),
        hourly_resets_at=_reset_at(primary),
        weekly_resets_at=_reset_at(secondary),
        queried_at=int(time.time()),
    )

    # Write back to meta.json
    meta = load_meta(account_id, config_dir)
    if meta:
        meta.quota = snapshot
        save_meta(meta, config_dir)

    return snapshot


async def refresh_subscription(
    account_id: str, config_dir: Path | None = None
) -> AccountMeta | None:
    """Fetch subscription info from accounts/check endpoint."""
    try:
        data = await _get_json_with_oauth_retry(
            ACCOUNTS_CHECK_URL,
            account_id,
            config_dir,
            _build_subscription_headers,
        )
        headers = await _build_subscription_headers(account_id, config_dir)
    except (OSError, ValueError, httpx.HTTPError, OAuthRefreshError) as error:
        logger.warning("Subscription fetch failed for %s: %s", account_id, error)
        if isinstance(error, OAuthReauthRequiredError):
            mark_requires_reauth(account_id, str(error), config_dir)
        return None

    snapshot = _parse_account_check(data, account_id, config_dir)
    if snapshot is None:
        snapshot = await _fetch_subscriptions_fallback(account_id, headers, config_dir)

    if snapshot:
        meta = load_meta(account_id, config_dir) or AccountMeta(id=account_id)
        meta.plan_type = snapshot.get("plan_type", meta.plan_type)
        meta.team_name = snapshot.get("team_name", meta.team_name)
        if snapshot.get("subscription_expires_at"):
            meta.subscription_expires_at = snapshot["subscription_expires_at"]
        if snapshot.get("email"):
            meta.email = snapshot["email"]
        if snapshot.get("name"):
            meta.name = snapshot["name"]
        save_meta(meta, config_dir)
        return meta

    return None


async def _get_json_with_oauth_retry(
    url: str,
    account_id: str,
    config_dir: Path | None,
    header_builder,
) -> dict:
    """GET authenticated JSON, refreshing once when upstream rejects access."""
    headers = await header_builder(account_id, config_dir)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(url, headers=headers)
        if response.status_code == 401:
            authorization = next(
                (value for name, value in headers.items() if name.lower() == "authorization"),
                "",
            )
            observed_access_token = authorization.removeprefix("Bearer ")
            await asyncio.to_thread(
                force_refresh_oauth_token,
                account_id,
                config_dir,
                observed_access_token=observed_access_token or None,
            )
            headers = await header_builder(account_id, config_dir)
            response = await client.get(url, headers=headers)
            if response.status_code == 401:
                raise OAuthReauthRequiredError("刷新凭据后仍被上游拒绝; 请重新登录")
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Upstream response is not a JSON object")
    return data


async def _build_quota_headers(account_id: str, config_dir: Path | None = None) -> dict:
    headers = build_auth_headers(account_id, config_dir)
    raw = load_auth_file(account_id, config_dir)
    auth = parse_auth_file(raw)
    validate_chatgpt_auth(auth)
    if auth.tokens:
        headers["ChatGPT-Account-Id"] = (
            extract_account_id_from_access_token(auth.tokens.access_token) or ""
        )
    headers["Accept"] = "application/json"
    return headers


async def _build_subscription_headers(account_id: str, config_dir: Path | None = None) -> dict:
    headers = build_auth_headers(account_id, config_dir)
    raw = load_auth_file(account_id, config_dir)
    auth = parse_auth_file(raw)
    validate_chatgpt_auth(auth)
    if auth.tokens:
        headers["ChatGPT-Account-Id"] = (
            extract_account_id_from_access_token(auth.tokens.access_token) or ""
        )
    headers["Accept"] = "application/json"
    headers["Referer"] = "https://chatgpt.com/"
    headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
    return headers


def _parse_account_check(
    data: dict, account_id: str, config_dir: Path | None = None
) -> dict | None:
    """Extract subscription info from accounts/check response."""
    records = _collect_records(data)
    if not records:
        return None

    # Find matching account
    preferred_id = _get_preferred_account_id(account_id, config_dir)
    selected = _find_matching_record(records, preferred_id, data)

    if not selected:
        return None

    account_obj = selected.get("account", selected)
    entitlement = selected.get("entitlement", {})

    plan_type = _first_str(entitlement, ["subscription_plan"])
    if not plan_type:
        plan_type = _first_str(account_obj, ["plan_type", "planType"])

    expires = _first_str(entitlement, ["expires_at"])
    if not expires:
        expires = _first_str(account_obj, ["expires_at"])

    name = _first_str(
        account_obj,
        [
            "name",
            "display_name",
            "account_name",
            "organization_name",
            "workspace_name",
            "title",
        ],
    )

    structure = _first_str(
        account_obj,
        [
            "structure",
            "account_structure",
            "kind",
            "type",
            "account_type",
        ],
    )

    email = _first_str(account_obj, ["email"])

    result = {
        "plan_type": plan_type or "",
        "team_name": structure or name or "",
        "name": name or "",
        "email": email or "",
    }

    if expires:
        with suppress(ValueError, TypeError):
            result["subscription_expires_at"] = _parse_timestamp(expires)

    return result


async def _fetch_subscriptions_fallback(
    account_id: str, headers: dict, config_dir: Path | None = None
) -> dict | None:
    """Fallback: try /backend-api/subscriptions."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(SUBSCRIPTIONS_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        plan = _first_str(data, ["subscription_plan", "plan_type"])
        expires = _first_str(data, ["active_until", "expires_at"])
        result = {"plan_type": plan or ""}
        if expires:
            with suppress(ValueError, TypeError):
                result["subscription_expires_at"] = _parse_timestamp(expires)
        return result
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Subscription fallback failed for %s: %s", account_id, error)
        return None


def _collect_records(data: dict) -> list[dict]:
    result = []
    accounts = data.get("accounts")
    if isinstance(accounts, list):
        result.extend(a for a in accounts if isinstance(a, dict))
    elif isinstance(accounts, dict):
        result.extend(v for v in accounts.values() if isinstance(v, dict))
    if not result and isinstance(data, list):
        result.extend(a for a in data if isinstance(a, dict))
    return result


def _get_preferred_account_id(account_id: str, config_dir: Path | None = None) -> str | None:
    meta = load_meta(account_id, config_dir)
    if meta and meta.email:
        return meta.email
    try:
        raw = load_auth_file(account_id, config_dir)
        from .auth import parse_auth_file

        auth = parse_auth_file(raw)
        if auth.tokens:
            return extract_account_id_from_access_token(auth.tokens.access_token)
    except (OSError, ValueError) as error:
        logger.warning("Cannot determine preferred account id for %s: %s", account_id, error)
    return None


def _find_matching_record(records: list[dict], preferred_id: str | None, data: dict) -> dict | None:
    if not records:
        return None
    for r in records:
        acct = r.get("account", r)
        candidates = [
            acct.get(k)
            for k in ["account_id", "id", "chatgpt_account_id", "workspace_id"]
            if acct.get(k)
        ]
        if preferred_id and any(c == preferred_id for c in candidates):
            return r

    ordering = data.get("account_ordering")
    if isinstance(ordering, list) and ordering:
        first = ordering[0]
        if isinstance(first, str):
            for r in records:
                if r.get("key") == first:
                    return r

    return records[0] if records else None


def _first_str(obj: dict, keys: list[str]) -> str | None:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _parse_timestamp(raw: str) -> int:
    raw = raw.strip()
    if raw.isdigit():
        ts = int(raw)
        if ts > 1e12:
            ts = ts // 1000
        elif ts < 1e9:
            ts = int(time.time() + ts)
        return ts
    # ISO format
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        return int(float(raw))
