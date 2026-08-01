"""Authentication helpers — read auth.json, build headers, refresh OAuth tokens."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
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


class OAuthRefreshError(RuntimeError):
    """The stored refresh token could not produce a new access token."""


_refresh_locks_guard = threading.Lock()
_refresh_locks: dict[str, threading.Lock] = {}


def _refresh_lock_for(account_id: str) -> threading.Lock:
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(account_id, threading.Lock())


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

    headers = {"Authorization": f"Bearer {access_token}"}
    if tokens.account_id:
        headers["ChatGPT-Account-Id"] = tokens.account_id
    return headers


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
    try:
        return force_refresh_oauth_token(
            account_id,
            config_dir,
            observed_access_token=tokens.access_token,
        )
    except OAuthRefreshError as error:
        logger.error("OAuth refresh failed for account %s: %s", account_id, error)
        return tokens.access_token


def _atomic_write_auth_file(path: Path, raw: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="auth.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(raw, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _refresh_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            code = error.get("code") or error.get("type")
            message = error.get("message")
        else:
            code = payload.get("code")
            message = error if isinstance(error, str) else payload.get("message")
        details = [str(value) for value in (code, message) if value]
        if details:
            return ": ".join(details)
    return response.reason_phrase or "unknown OAuth error"


def force_refresh_oauth_token(
    account_id: str,
    config_dir: Path | None = None,
    *,
    observed_access_token: str | None = None,
) -> str:
    """Force one refresh-token exchange and persist the rotated token chain.

    Concurrent callers provide the access token they observed. If another caller
    already advanced the account while this caller waited for the lock, the newer
    stored token is reused instead of spending the rotated refresh token twice.
    """
    with _refresh_lock_for(account_id):
        try:
            raw = load_auth_file(account_id, config_dir)
            auth = parse_auth_file(raw)
            validate_chatgpt_auth(auth)
        except (OSError, ValueError) as error:
            raise OAuthRefreshError(f"无法读取账号 auth.json: {error}") from error
        tokens = auth.tokens
        assert tokens is not None

        if observed_access_token and tokens.access_token != observed_access_token:
            return tokens.access_token
        if not tokens.refresh_token:
            raise OAuthRefreshError("账号缺少 refresh_token; 请重新登录")

        try:
            response = httpx.post(
                OAUTH_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": OAUTH_CLIENT_ID,
                    "refresh_token": tokens.refresh_token,
                },
                timeout=REFRESH_TIMEOUT,
            )
        except httpx.HTTPError as error:
            raise OAuthRefreshError(f"OAuth 刷新请求失败: {error}") from error

        if not response.is_success:
            detail = _refresh_error_detail(response)
            raise OAuthRefreshError(f"OAuth 刷新被拒绝 ({response.status_code}): {detail}")

        try:
            data = response.json()
        except ValueError as error:
            raise OAuthRefreshError("OAuth 刷新响应不是有效 JSON") from error
        if not isinstance(data, dict):
            raise OAuthRefreshError("OAuth 刷新响应格式无效")

        new_access = data.get("access_token")
        if not isinstance(new_access, str) or not new_access.strip():
            raise OAuthRefreshError("OAuth 刷新响应缺少 access_token")

        raw_tokens = raw.get("tokens")
        if not isinstance(raw_tokens, dict):
            raise OAuthRefreshError("账号 auth.json 缺少 tokens")
        raw_tokens["access_token"] = new_access
        new_refresh = data.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh.strip():
            raw_tokens["refresh_token"] = new_refresh
        new_id_token = data.get("id_token")
        if isinstance(new_id_token, str) and new_id_token.strip():
            raw_tokens["id_token"] = new_id_token
        raw["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())

        auth_path = account_dir(account_id, config_dir) / "auth.json"
        try:
            _atomic_write_auth_file(auth_path, raw)
        except (OSError, TypeError, ValueError) as error:
            raise OAuthRefreshError(f"无法更新账号 auth.json: {error}") from error
        return new_access
