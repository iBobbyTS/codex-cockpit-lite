"""Authentication helpers — read auth.json, build headers, refresh OAuth tokens."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
import jwt

from config import load_auth_file, account_dir
from models import AuthFile, AuthTokens, AuthMode

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_REFRESH_SKEW_SECONDS = 300
REFRESH_TIMEOUT = 25.0


def parse_auth_file(raw: dict) -> AuthFile:
    """Parse raw auth.json dict into AuthFile model."""
    if "OPENAI_API_KEY" in raw and "openai_api_key" not in raw:
        raw = {**raw, "openai_api_key": raw["OPENAI_API_KEY"]}
    return AuthFile(**raw)


def get_auth_mode(auth: AuthFile) -> AuthMode:
    if auth.agent_identity is not None:
        return AuthMode.AGENT_IDENTITY
    if auth.auth_mode == "apikey":
        return AuthMode.API_KEY
    if auth.tokens is not None and auth.tokens.access_token:
        return AuthMode.OAUTH
    if auth.OPENAI_API_KEY:
        return AuthMode.API_KEY
    return AuthMode.OAUTH


def build_auth_headers(account_id: str, config_dir: Optional[Path] = None) -> dict[str, str]:
    """Build Authorization and related headers for upstream requests."""
    raw = load_auth_file(account_id, config_dir)
    auth = parse_auth_file(raw)
    mode = get_auth_mode(auth)

    headers: dict[str, str] = {}

    if mode == AuthMode.API_KEY:
        key = auth.OPENAI_API_KEY or ""
        headers["Authorization"] = f"Bearer {key}"
        return headers

    if mode == AuthMode.AGENT_IDENTITY:
        identity = auth.agent_identity
        assertion = _build_agent_assertion(identity)
        headers["Authorization"] = f"Bearer {assertion}"
        return headers

    # OAuth
    tokens = auth.tokens
    if tokens is None:
        raise ValueError(f"Account {account_id} has no tokens")

    access_token = tokens.access_token
    if _token_expired(access_token):
        access_token = _refresh_oauth_token(tokens, account_id, config_dir)

    headers["Authorization"] = f"Bearer {access_token}"
    return headers


def build_search_headers(account_id: str, config_dir: Optional[Path] = None) -> dict[str, str]:
    """Build headers for /v1/alpha/search (requires ChatGPT web context)."""
    raw = load_auth_file(account_id, config_dir)
    auth = parse_auth_file(raw)
    tokens = auth.tokens
    if tokens is None:
        raise ValueError(f"OAuth tokens required for search, account {account_id}")

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
    except Exception:
        return ""


def extract_account_id_from_access_token(access_token: str) -> Optional[str]:
    try:
        payload = jwt.decode(access_token, options={"verify_signature": False})
        auth_data = payload.get("https://api.openai.com/auth", {})
        if isinstance(auth_data, dict):
            return auth_data.get("account_id")
    except Exception:
        pass
    return None


def _token_expired(token: str) -> bool:
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp", 0)
        return (exp - TOKEN_REFRESH_SKEW_SECONDS) < time.time()
    except Exception:
        return True


def _refresh_oauth_token(
    tokens: AuthTokens, account_id: str, config_dir: Optional[Path] = None
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
                raw["last_refresh"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime()
                )
                tmp = ad / "auth.tmp"
                tmp.write_text(json.dumps(raw, indent=2))
                tmp.replace(auth_path)

        return new_access
    except Exception as e:
        logger.error("OAuth refresh failed for account %s: %s", account_id, e)
        return tokens.access_token


def _build_agent_assertion(identity) -> str:
    """Build a minimal Agent Identity assertion JWT using Ed25519."""
    now = int(time.time())
    header = {"alg": "EdDSA", "typ": "JWT"}
    payload = {
        "iss": identity.agent_runtime_id,
        "sub": identity.chatgpt_user_id,
        "aud": "https://auth.openai.com/api/accounts",
        "iat": now,
        "exp": now + 300,
        "account_id": identity.account_id,
    }

    try:
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        key_bytes = base64.b64decode(identity.agent_private_key)
        private_key = serialization.load_der_private_key(key_bytes, password=None)
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise ValueError("Agent private key is not Ed25519")
        return jwt.encode(payload, private_key, algorithm="EdDSA", headers=header)
    except ImportError:
        logger.error("cryptography package required for Agent Identity signing")
        raise
