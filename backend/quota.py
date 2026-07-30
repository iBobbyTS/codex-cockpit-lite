"""Quota and subscription information fetcher."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from auth import build_auth_headers, extract_account_id_from_access_token
from config import load_auth_file, load_meta, save_meta, accounts_dir
from models import AccountMeta, QuotaSnapshot, AuthMode

logger = logging.getLogger(__name__)

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
ACCOUNTS_CHECK_URL = (
    "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
)
SUBSCRIPTIONS_URL = "https://chatgpt.com/backend-api/subscriptions"
QUOTA_REFRESH_INTERVAL_SECONDS = 300
REQUEST_TIMEOUT = 30.0


async def refresh_quota(account_id: str, config_dir: Optional[Path] = None) -> QuotaSnapshot:
    """Fetch quota from wham/usage endpoint."""
    try:
        headers = await _build_quota_headers(account_id, config_dir)
    except Exception as e:
        logger.warning("Cannot build headers for quota query %s: %s", account_id, e)
        return QuotaSnapshot()

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(USAGE_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Quota fetch failed for %s: %s", account_id, e)
        return QuotaSnapshot()

    rate_limit = data.get("rate_limit", {}) or {}
    primary = rate_limit.get("primary_window", {}) or {}
    secondary = rate_limit.get("secondary_window", {}) or {}

    def _pct(window: dict) -> int:
        used = window.get("used_percent", 0) or 0
        return max(0, min(100, 100 - int(used)))

    def _reset_at(window: dict) -> Optional[int]:
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
    account_id: str, config_dir: Optional[Path] = None
) -> Optional[AccountMeta]:
    """Fetch subscription info from accounts/check endpoint."""
    try:
        headers = await _build_subscription_headers(account_id, config_dir)
    except Exception as e:
        logger.warning("Cannot build headers for subscription query %s: %s", account_id, e)
        return None

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.get(ACCOUNTS_CHECK_URL, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Subscription fetch failed for %s: %s", account_id, e)
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


async def _build_quota_headers(account_id: str, config_dir: Optional[Path] = None) -> dict:
    headers = build_auth_headers(account_id, config_dir)
    raw = load_auth_file(account_id, config_dir)
    from auth import parse_auth_file, get_auth_mode

    auth = parse_auth_file(raw)
    mode = get_auth_mode(auth)
    if mode == AuthMode.OAUTH and auth.tokens:
        headers["ChatGPT-Account-Id"] = (
            extract_account_id_from_access_token(auth.tokens.access_token) or ""
        )
    headers["Accept"] = "application/json"
    return headers


async def _build_subscription_headers(account_id: str, config_dir: Optional[Path] = None) -> dict:
    headers = build_auth_headers(account_id, config_dir)
    raw = load_auth_file(account_id, config_dir)
    from auth import parse_auth_file, get_auth_mode

    auth = parse_auth_file(raw)
    mode = get_auth_mode(auth)
    if mode == AuthMode.OAUTH and auth.tokens:
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
    data: dict, account_id: str, config_dir: Optional[Path] = None
) -> Optional[dict]:
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

    name = _first_str(account_obj, [
        "name", "display_name", "account_name",
        "organization_name", "workspace_name", "title",
    ])

    structure = _first_str(account_obj, [
        "structure", "account_structure", "kind", "type", "account_type",
    ])

    email = _first_str(account_obj, ["email"])

    result = {
        "plan_type": plan_type or "",
        "team_name": structure or name or "",
        "name": name or "",
        "email": email or "",
    }

    if expires:
        try:
            result["subscription_expires_at"] = _parse_timestamp(expires)
        except (ValueError, TypeError):
            pass

    return result


async def _fetch_subscriptions_fallback(
    account_id: str, headers: dict, config_dir: Optional[Path] = None
) -> Optional[dict]:
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
            try:
                result["subscription_expires_at"] = _parse_timestamp(expires)
            except (ValueError, TypeError):
                pass
        return result
    except Exception:
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


def _get_preferred_account_id(account_id: str, config_dir: Optional[Path] = None) -> Optional[str]:
    meta = load_meta(account_id, config_dir)
    if meta and meta.email:
        return meta.email
    try:
        raw = load_auth_file(account_id, config_dir)
        from auth import parse_auth_file, get_auth_mode
        auth = parse_auth_file(raw)
        if auth.tokens:
            return extract_account_id_from_access_token(auth.tokens.access_token)
    except Exception:
        pass
    return None


def _find_matching_record(records: list[dict], preferred_id: Optional[str], data: dict) -> Optional[dict]:
    if not records:
        return None
    for r in records:
        acct = r.get("account", r)
        candidates = [
            acct.get(k) for k in
            ["account_id", "id", "chatgpt_account_id", "workspace_id"]
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


def _first_str(obj: dict, keys: list[str]) -> Optional[str]:
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
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return int(float(raw))
