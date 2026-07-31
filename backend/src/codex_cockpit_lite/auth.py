"""Authentication helpers — read auth.json, build headers, refresh OAuth tokens."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx
import jwt

from .config import account_dir, load_auth_file
from .models import AuthFile, AuthTokens

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_REFRESH_SKEW_SECONDS = 300
REFRESH_TIMEOUT = 25.0


def parse_auth_file(raw: dict) -> AuthFile:
    """Parse raw auth.json dict into AuthFile model."""
    return AuthFile(**raw)


def validate_chatgpt_auth(auth: AuthFile) -> None:
    """Reject every auth format except the official ChatGPT login format."""
    if auth.auth_mode != "chatgpt":
        raise ValueError("Codex Cockpit Lite 只支持 ChatGPT 登录")
    if auth.tokens is None:
        raise ValueError("ChatGPT 登录缺少 tokens")


def build_auth_headers(account_id: str, config_dir: Path | None = None) -> dict[str, str]:
    """Build Authorization and related headers for upstream requests."""
    raw = load_auth_file(account_id, config_dir)
    auth = parse_auth_file(raw)
    validate_chatgpt_auth(auth)
    tokens = auth.tokens
    assert tokens is not None

    access_token = tokens.access_token
    if _token_expired(access_token):
        access_token = _refresh_oauth_token(tokens, account_id, config_dir)

    return {"Authorization": f"Bearer {access_token}"}


def build_search_headers(account_id: str, config_dir: Path | None = None) -> dict[str, str]:
    """Build headers for /v1/alpha/search (requires ChatGPT web context)."""
    raw = load_auth_file(account_id, config_dir)
    auth = parse_auth_file(raw)
    validate_chatgpt_auth(auth)
    tokens = auth.tokens
    assert tokens is not None

    access_token = tokens.access_token
    if _token_expired(access_token):
        access_token = _refresh_oauth_token(tokens, account_id, config_dir)

    return {
        "Authorization": f"Bearer {access_token}",
        "Referer": "https://chatgpt.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
    }


def extract_email_from_id_token(id_token: str) -> str:
    try:
        payload = jwt.decode(id_token, options={"verify_signature": False})
        return payload.get("email", "")
    except jwt.DecodeError, TypeError:
        return ""


def extract_account_id_from_access_token(access_token: str) -> str | None:
    try:
        payload = jwt.decode(access_token, options={"verify_signature": False})
        auth_data = payload.get("https://api.openai.com/auth", {})
        if isinstance(auth_data, dict):
            return auth_data.get("account_id")
    except jwt.DecodeError, TypeError:
        logger.debug("Unable to decode account id from access token")
    return None


def _token_expired(token: str) -> bool:
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp", 0)
        return (exp - TOKEN_REFRESH_SKEW_SECONDS) < time.time()
    except jwt.DecodeError, TypeError:
        return True


def _refresh_oauth_token(
    tokens: AuthTokens, account_id: str, config_dir: Path | None = None
) -> str:
    if not tokens.refresh_token:
        logger.warning("Account %s: no refresh_token, cannot refresh", account_id)
        return tokens.access_token

    try:
        resp = httpx.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": OAUTH_CLIENT_ID,
                "refresh_token": tokens.refresh_token,
            },
            timeout=REFRESH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        new_access = data.get("access_token", tokens.access_token)
        new_refresh = data.get("refresh_token", tokens.refresh_token)

        # Write back to auth.json
        ad = account_dir(account_id, config_dir)
        auth_path = ad / "auth.json"
        if auth_path.exists():
            raw = json.loads(auth_path.read_text())
            if "tokens" in raw:
                raw["tokens"]["access_token"] = new_access
                if new_refresh:
                    raw["tokens"]["refresh_token"] = new_refresh
                raw["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
                tmp = ad / "auth.tmp"
                tmp.write_text(json.dumps(raw, indent=2))
                tmp.replace(auth_path)

        return new_access
    except (httpx.HTTPError, OSError, ValueError, KeyError, json.JSONDecodeError) as e:
        logger.error("OAuth refresh failed for account %s: %s", account_id, e)
        return tokens.access_token
