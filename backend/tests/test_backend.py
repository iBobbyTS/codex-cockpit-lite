"""Tests for Codex Cockpit Lite backend."""

from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import httpx
import jwt
import pytest

from codex_cockpit_lite.api import get_config_dir_info, set_api_config_dir
from codex_cockpit_lite.auth import (
    _token_expired,
    parse_auth_file,
    validate_chatgpt_auth,
)
from codex_cockpit_lite.config import (
    get_config_dir,
    list_selected_accounts,
    load_config,
    load_meta,
    save_config,
    save_meta,
)
from codex_cockpit_lite.main import app, find_available_port, frontend_dist_path
from codex_cockpit_lite.models import (
    AccountMeta,
    ApiConfig,
    AppConfig,
    AuthMode,
    CockpitStatus,
    QuotaSnapshot,
    SpeedMode,
)
from codex_cockpit_lite.quota import _collect_records, _parse_timestamp


def chatgpt_auth(email: str = "test@example.com") -> dict:
    key = "test-key-with-at-least-thirty-two-bytes"
    id_token = jwt.encode({"email": email}, key, algorithm="HS256")
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": id_token,
            "access_token": jwt.encode({"exp": int(time.time()) + 3600}, key, algorithm="HS256"),
            "refresh_token": "refresh-token",
            "account_id": "account-1",
        },
    }


def test_default_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.version == 1
    assert cfg.api.port == 8844
    assert cfg.api.speed == SpeedMode.STANDARD
    assert cfg.api.selected_accounts == []
    assert cfg.api.auto_switch.enabled is True


def test_save_and_reload_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    cfg.api.port = 9999
    cfg.api.speed = SpeedMode.FAST
    save_config(cfg, tmp_path)

    reloaded = load_config(tmp_path)
    assert reloaded.api.port == 9999
    assert reloaded.api.speed == SpeedMode.FAST


def test_config_dir_endpoint_reports_backend_path(tmp_path: Path) -> None:
    set_api_config_dir(tmp_path)
    try:
        assert get_config_dir_info() == {"path": str(tmp_path)}
    finally:
        set_api_config_dir(get_config_dir())


def test_account_meta_roundtrip(tmp_path: Path) -> None:
    meta = AccountMeta(
        id="test-1",
        name="Test Account",
        email="test@openai.com",
        auth_mode=AuthMode.OAUTH,
        plan_type="pro",
        team_name="Personal",
        quota=QuotaSnapshot(weekly_percent=80, hourly_percent=30),
    )
    save_meta(meta, tmp_path)
    loaded = load_meta("test-1", tmp_path)
    assert loaded is not None
    assert loaded.email == "test@openai.com"
    assert loaded.quota.weekly_percent == 80


def test_list_selected_accounts(tmp_path: Path) -> None:
    for index in range(2):
        save_meta(AccountMeta(id=f"acc-{index}", email=f"user{index}@openai.com"), tmp_path)

    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = ["acc-0"]
    save_config(cfg, tmp_path)

    assert [account.id for account in list_selected_accounts(tmp_path)] == ["acc-0"]


def test_parse_chatgpt_auth_file() -> None:
    auth = parse_auth_file(chatgpt_auth())
    validate_chatgpt_auth(auth)
    assert auth.tokens is not None


@pytest.mark.parametrize(
    "auth_mode", ["api", "apikey", "agentIdentity", "ChatGPT", "CHATGPT", None]
)
def test_only_exact_chatgpt_auth_mode_is_allowed(auth_mode: str | None) -> None:
    raw = chatgpt_auth()
    raw["auth_mode"] = auth_mode
    with pytest.raises(ValueError, match="只支持 ChatGPT 登录"):
        validate_chatgpt_auth(parse_auth_file(raw))


def test_token_expiration() -> None:
    key = "test-key-with-at-least-thirty-two-bytes"
    valid = jwt.encode({"exp": int(time.time()) + 3600}, key, algorithm="HS256")
    expired = jwt.encode({"exp": int(time.time()) - 100}, key, algorithm="HS256")
    assert _token_expired(valid) is False
    assert _token_expired(expired) is True


def test_app_config_serialization() -> None:
    cfg = AppConfig(api=ApiConfig(port=1456, speed=SpeedMode.FAST))
    data = cfg.model_dump()
    assert data["api"]["port"] == 1456
    assert AppConfig(**data).api.speed == SpeedMode.FAST


def test_cockpit_status_model() -> None:
    status = CockpitStatus(running=True, uptime_seconds=10.5)
    assert status.model_dump()["running"] is True


def test_parse_timestamp() -> None:
    timestamp = int(time.time()) + 86400
    assert _parse_timestamp(str(timestamp)) == timestamp
    assert _parse_timestamp(str(timestamp * 1000)) == timestamp
    assert _parse_timestamp("2026-12-31T00:00:00Z") > 0


def test_collect_records() -> None:
    mapping = {"accounts": {"default": {"account": {"plan_type": "pro"}}}}
    sequence = {"accounts": [{"account": {"plan_type": "team"}}]}
    assert _collect_records(mapping)[0]["account"]["plan_type"] == "pro"
    assert _collect_records(sequence)[0]["account"]["plan_type"] == "team"


def test_find_available_port_skips_occupied() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 18844))
        sock.listen(1)
        assert find_available_port(18844) == 18845


def test_frontend_dist_path_in_source() -> None:
    assert frontend_dist_path().name == "dist"
    assert frontend_dist_path().parent.name == "frontend"


def test_frontend_dist_path_when_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert frontend_dist_path() == tmp_path / "frontend" / "dist"


@pytest.mark.asyncio
async def test_refresh_missing_account_returns_404(tmp_path: Path) -> None:
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/accounts/missing/refresh")
        assert response.status_code == 404
        assert response.json()["detail"] == "账号 missing 不存在"
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_invalid_auth_mode_returns_readable_error_without_writing(tmp_path: Path) -> None:
    set_api_config_dir(tmp_path)
    raw = chatgpt_auth()
    raw["auth_mode"] = "api"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/accounts/import", json={"auth_json": json.dumps(raw)}
            )
        assert response.status_code == 400
        assert response.json()["detail"] == "Codex Cockpit Lite 只支持 ChatGPT 登录"
        accounts_dir = tmp_path / "accounts"
        assert not accounts_dir.exists() or list(accounts_dir.iterdir()) == []
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_lifespan_cancels_quota_refresh_task() -> None:
    async with app.router.lifespan_context(app):
        task = app.state.quota_refresh_task
        assert task.done() is False
    assert task.cancelled() is True
