"""Tests for Codex Cockpit Lite backend."""

from __future__ import annotations

import gzip
import json
import socket
import sys
import time
from pathlib import Path

import httpx
import jwt
import pytest
import uvicorn

import codex_cockpit_lite.main as main_module
import codex_cockpit_lite.proxy as proxy_module
import codex_cockpit_lite.quota as quota_module
import codex_cockpit_lite.status as status_module
from codex_cockpit_lite.api import get_config_dir_info, set_api_config_dir
from codex_cockpit_lite.auth import (
    _token_expired,
    build_auth_headers,
    force_refresh_oauth_token,
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
from codex_cockpit_lite.proxy import (
    _build_downstream_headers,
    _build_upstream_headers,
    _stream_upstream_response,
    _with_original_query,
    activate_account,
    get_active_account,
    is_account_schedulable,
    switch_to_next_account,
)
from codex_cockpit_lite.quota import _collect_records, _parse_timestamp
from codex_cockpit_lite.status import build_service_url, find_active_lan_address


def chatgpt_auth(email: str = "test@example.com", account_id: str = "account-1") -> dict:
    key = "test-key-with-at-least-thirty-two-bytes"
    id_token = jwt.encode({"email": email}, key, algorithm="HS256")
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": id_token,
            "access_token": jwt.encode({"exp": int(time.time()) + 3600}, key, algorithm="HS256"),
            "refresh_token": "refresh-token",
            "account_id": account_id,
        },
    }


def test_default_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg.version == 1
    assert cfg.api.port == 8844
    assert cfg.api.speed == SpeedMode.STANDARD
    assert cfg.api.api_key == "sandrone"
    assert cfg.api.account_order == []
    assert cfg.api.selected_accounts == []
    assert cfg.api.auto_switch.enabled is True


def test_save_and_reload_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    cfg.api.port = 9999
    cfg.api.speed = SpeedMode.FAST
    cfg.api.api_key = "changed-api-key"
    save_config(cfg, tmp_path)

    reloaded = load_config(tmp_path)
    assert reloaded.api.port == 9999
    assert reloaded.api.speed == SpeedMode.FAST
    assert reloaded.api.api_key == "changed-api-key"


def test_legacy_password_config_migrates_to_api_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "api": {
                    "password": "legacy-key",
                },
            }
        )
    )

    cfg = load_config(tmp_path)
    saved = json.loads(path.read_text())

    assert cfg.api.api_key == "legacy-key"
    assert saved["api"]["api_key"] == "legacy-key"
    assert "password" not in saved["api"]


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
        display_name="My Account",
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
    assert loaded.display_name == "My Account"
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


def test_auth_headers_include_codex_oauth_context(tmp_path: Path) -> None:
    account_path = tmp_path / "accounts" / "account-1"
    account_path.mkdir(parents=True)
    (account_path / "auth.json").write_text(json.dumps(chatgpt_auth()))

    headers = build_auth_headers("account-1", tmp_path)

    assert headers["Authorization"].startswith("Bearer ")
    assert headers["ChatGPT-Account-Id"] == "account-1"
    assert "Originator" not in headers


def test_force_refresh_updates_rotated_tokens_in_account_auth_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account_path = tmp_path / "accounts" / "account-1"
    account_path.mkdir(parents=True)
    raw = chatgpt_auth()
    old_access = raw["tokens"]["access_token"]
    (account_path / "auth.json").write_text(json.dumps(raw))

    calls = 0

    def fake_post(url, *, data, timeout):
        nonlocal calls
        calls += 1
        assert url == "https://auth.openai.com/oauth/token"
        assert data["refresh_token"] == "refresh-token"
        assert timeout == 25.0
        return httpx.Response(
            200,
            json={
                "access_token": "new-access-token",
                "refresh_token": "rotated-refresh-token",
                "id_token": "new-id-token",
            },
        )

    monkeypatch.setattr("codex_cockpit_lite.auth.httpx.post", fake_post)

    refreshed = force_refresh_oauth_token("account-1", tmp_path, observed_access_token=old_access)
    reused = force_refresh_oauth_token("account-1", tmp_path, observed_access_token=old_access)
    saved = json.loads((account_path / "auth.json").read_text())

    assert refreshed == "new-access-token"
    assert reused == "new-access-token"
    assert calls == 1
    assert saved["tokens"] == {
        "id_token": "new-id-token",
        "access_token": "new-access-token",
        "refresh_token": "rotated-refresh-token",
        "account_id": "account-1",
    }
    assert saved["last_refresh"].endswith("Z")
    assert not list(account_path.glob("auth.*.tmp"))


def test_upstream_headers_replace_case_variant_auth_without_duplicates() -> None:
    headers = _build_upstream_headers(
        {
            "authorization": "Bearer inbound",
            "originator": "Codex Desktop",
            "content-length": "42",
            "x-openai-internal-codex-responses-lite": "true",
        },
        {
            "Authorization": "Bearer selected-account",
            "ChatGPT-Account-Id": "account-1",
        },
    )

    assert headers["authorization"] == "Bearer selected-account"
    assert headers["chatgpt-account-id"] == "account-1"
    assert headers["originator"] == "Codex Desktop"
    assert headers["x-openai-internal-codex-responses-lite"] == "true"
    assert "content-length" not in headers
    assert len([name for name in headers if name.lower() == "authorization"]) == 1
    assert len([name for name in headers if name.lower() == "originator"]) == 1


def test_decoded_downstream_headers_drop_stale_transport_metadata() -> None:
    headers = _build_downstream_headers(
        httpx.Headers(
            {
                "Content-Encoding": "gzip",
                "Content-Length": "123",
                "Transfer-Encoding": "chunked",
                "X-Request-Id": "request-1",
            }
        ),
        decoded=True,
    )

    assert headers == {"x-request-id": "request-1"}


def test_upstream_url_preserves_client_query() -> None:
    request = httpx.Request("GET", "http://127.0.0.1:8844/v1/models?client_version=0.146.0")

    result = _with_original_query(request, "https://chatgpt.com/backend-api/codex/models")

    assert result == "https://chatgpt.com/backend-api/codex/models?client_version=0.146.0"


@pytest.mark.asyncio
async def test_streaming_keeps_raw_encoding_and_closes_upstream() -> None:
    class FakeResponse:
        closed = False

        async def aiter_raw(self):
            yield b"compressed-"
            yield b"bytes"

        async def aclose(self):
            self.closed = True

    class FakeClient:
        closed = False

        async def aclose(self):
            self.closed = True

    response = FakeResponse()
    client = FakeClient()

    chunks = [chunk async for chunk in _stream_upstream_response(response, client)]

    assert chunks == [b"compressed-", b"bytes"]
    assert response.closed is True
    assert client.closed is True


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
    assert "quota_threshold_percent" not in data["api"]["auto_switch"]


def test_legacy_quota_threshold_is_removed_from_config_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "api": {
                    "auto_switch": {
                        "enabled": True,
                        "strategy": "sequential",
                        "quota_threshold_percent": 95,
                    }
                },
            }
        )
    )

    cfg = load_config(tmp_path)

    assert "quota_threshold_percent" not in cfg.model_dump()["api"]["auto_switch"]
    assert "quota_threshold_percent" not in json.loads(path.read_text())["api"]["auto_switch"]


def test_scheduler_requires_both_quotas_and_cycles_in_config_order(tmp_path: Path) -> None:
    accounts = [
        AccountMeta(
            id="exhausted",
            email="exhausted@example.com",
            quota=QuotaSnapshot(weekly_percent=0, hourly_percent=100),
        ),
        AccountMeta(
            id="second",
            email="second@example.com",
            quota=QuotaSnapshot(weekly_percent=80, hourly_percent=70),
        ),
        AccountMeta(
            id="third",
            email="third@example.com",
            quota=QuotaSnapshot(weekly_percent=60, hourly_percent=50),
        ),
    ]
    for account in accounts:
        save_meta(account, tmp_path)
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = [account.id for account in accounts]
    save_config(cfg, tmp_path)

    assert is_account_schedulable(accounts[0]) is False
    assert get_active_account(tmp_path)["id"] == "second"
    assert activate_account("third", tmp_path)["id"] == "third"
    assert switch_to_next_account(tmp_path)["id"] == "second"


@pytest.mark.asyncio
async def test_rate_limit_refreshes_accounts_in_order_and_keeps_current_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for account_id in ["first", "second", "third"]:
        save_meta(
            AccountMeta(
                id=account_id,
                email=f"{account_id}@example.com",
                quota=QuotaSnapshot(weekly_percent=50, hourly_percent=50),
            ),
            tmp_path,
        )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = ["first", "second", "third"]
    save_config(cfg, tmp_path)
    activate_account("second", tmp_path)
    refresh_order: list[str] = []

    async def fake_refresh(account_id: str, config_dir=None) -> QuotaSnapshot:
        assert config_dir == tmp_path
        refresh_order.append(account_id)
        return QuotaSnapshot(weekly_percent=25, hourly_percent=25, queried_at=1)

    monkeypatch.setattr(proxy_module, "refresh_quota", fake_refresh)

    selected = await proxy_module._refresh_quotas_and_select_account("second", tmp_path)

    assert refresh_order == ["first", "second", "third"]
    assert selected["id"] == "second"
    assert get_active_account(tmp_path)["id"] == "second"


@pytest.mark.asyncio
async def test_rate_limit_switches_after_refresh_or_reports_all_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for account_id in ["first", "second", "third"]:
        save_meta(
            AccountMeta(
                id=account_id,
                email=f"{account_id}@example.com",
                quota=QuotaSnapshot(weekly_percent=50, hourly_percent=50),
            ),
            tmp_path,
        )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = ["first", "second", "third"]
    save_config(cfg, tmp_path)
    activate_account("first", tmp_path)
    remaining = {
        "first": QuotaSnapshot(weekly_percent=0, hourly_percent=50, queried_at=1),
        "second": QuotaSnapshot(weekly_percent=40, hourly_percent=30, queried_at=1),
        "third": QuotaSnapshot(weekly_percent=20, hourly_percent=10, queried_at=1),
    }

    async def fake_refresh(account_id: str, config_dir=None) -> QuotaSnapshot:
        assert config_dir == tmp_path
        return remaining[account_id]

    monkeypatch.setattr(proxy_module, "refresh_quota", fake_refresh)

    selected = await proxy_module._refresh_quotas_and_select_account("first", tmp_path)
    assert selected["id"] == "second"

    remaining.update(
        {
            account_id: QuotaSnapshot(weekly_percent=0, hourly_percent=0, queried_at=2)
            for account_id in remaining
        }
    )
    selected = await proxy_module._refresh_quotas_and_select_account("second", tmp_path)
    assert selected is None
    assert proxy_module._active_account_id == ""


@pytest.mark.asyncio
async def test_force_activate_and_reorder_accounts(tmp_path: Path) -> None:
    for account_id in ["first", "second", "third"]:
        save_meta(
            AccountMeta(
                id=account_id,
                email=f"{account_id}@example.com",
                quota=QuotaSnapshot(weekly_percent=100, hourly_percent=100),
            ),
            tmp_path,
        )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = ["first", "second", "third"]
    save_config(cfg, tmp_path)
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            activated = await client.post("/api/accounts/second/activate")
            reordered = await client.put(
                "/api/accounts/order",
                json={"account_ids": ["second", "third", "first"]},
            )
            listed = await client.get("/api/accounts")

        assert activated.status_code == 200
        assert activated.json()["active_account_id"] == "second"
        assert reordered.status_code == 200
        saved_config = load_config(tmp_path)
        assert saved_config.api.account_order == ["second", "third", "first"]
        assert saved_config.api.selected_accounts == ["second", "third", "first"]
        assert [account["id"] for account in listed.json()] == ["second", "third", "first"]
        assert [account["is_active"] for account in listed.json()] == [True, False, False]
        assert switch_to_next_account(tmp_path)["id"] == "third"
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_disabled_account_can_be_reordered_and_is_skipped_by_scheduler(
    tmp_path: Path,
) -> None:
    for account_id in ["first", "disabled", "third"]:
        save_meta(
            AccountMeta(
                id=account_id,
                email=f"{account_id}@example.com",
                quota=QuotaSnapshot(weekly_percent=100, hourly_percent=100),
            ),
            tmp_path,
        )
    cfg = load_config(tmp_path)
    cfg.api.account_order = ["first", "disabled", "third"]
    cfg.api.selected_accounts = ["first", "third"]
    save_config(cfg, tmp_path)
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            reordered = await client.put(
                "/api/accounts/order",
                json={"account_ids": ["third", "disabled", "first"]},
            )
            listed = await client.get("/api/accounts")

        assert reordered.status_code == 200
        saved_config = load_config(tmp_path)
        assert saved_config.api.account_order == ["third", "disabled", "first"]
        assert saved_config.api.selected_accounts == ["third", "first"]
        assert [account["id"] for account in listed.json()] == ["third", "disabled", "first"]
        assert [account["enabled"] for account in listed.json()] == [True, False, True]
        assert activate_account("third", tmp_path)["id"] == "third"
        assert switch_to_next_account(tmp_path)["id"] == "first"

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            enabled = await client.put("/api/accounts/disabled/toggle", json={"enabled": True})
        assert enabled.status_code == 200
        assert load_config(tmp_path).api.selected_accounts == ["third", "disabled", "first"]
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_force_activate_rejects_account_with_any_exhausted_quota(tmp_path: Path) -> None:
    save_meta(
        AccountMeta(
            id="exhausted",
            quota=QuotaSnapshot(weekly_percent=100, hourly_percent=0),
        ),
        tmp_path,
    )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = ["exhausted"]
    save_config(cfg, tmp_path)
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/accounts/exhausted/activate")
            listed = await client.get("/api/accounts")

        assert response.status_code == 400
        assert response.json()["detail"] == "账号的 5h 和 7d 剩余额度必须都大于 0"
        assert listed.json()[0]["schedulable"] is False
        assert listed.json()[0]["is_active"] is False
    finally:
        set_api_config_dir(get_config_dir())


def test_cockpit_status_model() -> None:
    status = CockpitStatus(
        running=True,
        uptime_seconds=10.5,
        service_url="http://127.0.0.1:8844/v1",
    )
    assert status.model_dump()["running"] is True
    assert status.model_dump()["service_url"] == "http://127.0.0.1:8844/v1"


def test_active_lan_address_uses_first_usable_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        status_module,
        "_active_ipv4_candidates",
        lambda: iter(["127.0.0.1", "169.254.1.2", "192.168.1.25", "10.0.0.8"]),
    )
    assert find_active_lan_address() == "192.168.1.25"


def test_active_lan_address_falls_back_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_module, "_active_ipv4_candidates", lambda: iter([]))
    assert find_active_lan_address() == "127.0.0.1"


def test_service_url_uses_loopback_for_local_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_lookup() -> str:
        raise AssertionError("local bind must not inspect network interfaces")

    monkeypatch.setattr(status_module, "find_active_lan_address", unexpected_lookup)
    assert build_service_url("127.0.0.1", 8844) == "http://127.0.0.1:8844/v1"


def test_service_url_uses_active_address_for_lan_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(status_module, "find_active_lan_address", lambda: "192.168.50.12")
    assert build_service_url("0.0.0.0", 8845) == "http://192.168.50.12:8845/v1"


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
async def test_models_api_route_precedes_spa_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_proxy_models(request, config_dir):
        del request, config_dir
        return main_module.JSONResponse({"object": "list", "data": []})

    monkeypatch.setattr(proxy_module, "proxy_models", fake_proxy_models)
    monkeypatch.setattr(main_module, "_config_dir", tmp_path)
    save_config(AppConfig(api=ApiConfig(api_key="route-key")), tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models", headers={"Authorization": "Bearer route-key"})

    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}


@pytest.mark.asyncio
async def test_served_api_accepts_only_configured_original_protocol_key_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxy_calls = 0

    async def fake_proxy_models(request, config_dir):
        nonlocal proxy_calls
        del request, config_dir
        proxy_calls += 1
        return main_module.JSONResponse({"object": "list", "data": []})

    monkeypatch.setattr(proxy_module, "proxy_models", fake_proxy_models)
    monkeypatch.setattr(main_module, "_config_dir", tmp_path)
    save_config(AppConfig(api=ApiConfig(api_key="only-this-key")), tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get("/v1/models"),
            await client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"}),
            await client.get("/v1/models", headers={"X-API-Key": "wrong-key"}),
        ]
        bearer = await client.get("/v1/models", headers={"Authorization": "Bearer only-this-key"})
        x_api_key = await client.get("/v1/models", headers={"X-API-Key": "only-this-key"})

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert all(response.json() == {"error": {"message": "Unauthorized"}} for response in responses)
    assert bearer.status_code == 200
    assert x_api_key.status_code == 200
    assert proxy_calls == 2


@pytest.mark.asyncio
async def test_empty_configured_api_key_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main_module, "_config_dir", tmp_path)
    save_config(AppConfig(api=ApiConfig(api_key="")), tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models", headers={"X-API-Key": ""})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_backend_search_alias_requires_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(main_module, "_config_dir", tmp_path)
    save_config(AppConfig(api=ApiConfig(api_key="search-key")), tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/backend-api/codex/alpha/search", json={})

    assert response.status_code == 401
    assert response.json() == {"error": {"message": "Unauthorized"}}


@pytest.mark.asyncio
async def test_responses_use_chatgpt_codex_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_proxy(request, upstream_url, config_dir=None, is_sse=False, max_retries=8):
        del request, config_dir, max_retries
        captured.update(upstream_url=upstream_url, is_sse=is_sse)
        return main_module.Response(status_code=204)

    monkeypatch.setattr(proxy_module, "_proxy_with_retry", fake_proxy)

    response = await proxy_module.proxy_responses(object())

    assert response.status_code == 204
    assert captured == {
        "upstream_url": "https://chatgpt.com/backend-api/codex/responses",
        "is_sse": True,
    }


@pytest.mark.asyncio
async def test_models_use_chatgpt_codex_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_proxy(request, upstream_url, config_dir=None, is_sse=False, max_retries=8):
        del request, config_dir, max_retries
        captured.update(upstream_url=upstream_url, is_sse=is_sse)
        return main_module.Response(status_code=204)

    monkeypatch.setattr(proxy_module, "_proxy_with_retry", fake_proxy)

    response = await proxy_module.proxy_models(object())

    assert response.status_code == 204
    assert captured == {
        "upstream_url": "https://chatgpt.com/backend-api/codex/models",
        "is_sse": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("is_sse", [False, True])
async def test_401_forces_oauth_refresh_updates_account_auth_and_retries_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, is_sse: bool
) -> None:
    account_id = "account-1"
    account_path = tmp_path / "accounts" / account_id
    account_path.mkdir(parents=True)
    raw = chatgpt_auth()
    old_access = raw["tokens"]["access_token"]
    new_access = jwt.encode(
        {"exp": int(time.time()) + 7200},
        "test-key-with-at-least-thirty-two-bytes",
        algorithm="HS256",
    )
    (account_path / "auth.json").write_text(json.dumps(raw))
    save_meta(
        AccountMeta(
            id=account_id,
            email="test@example.com",
            quota=QuotaSnapshot(weekly_percent=50, hourly_percent=50),
        ),
        tmp_path,
    )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = [account_id]
    save_config(cfg, tmp_path)
    activate_account(account_id, tmp_path)

    upstream_authorizations: list[str] = []
    refresh_calls = 0

    class FakeStream(httpx.AsyncByteStream):
        def __init__(self, content: bytes) -> None:
            self.content = content

        async def __aiter__(self):
            yield self.content

    class FakeRequest:
        method = "POST"

        class url:
            path = "/v1/responses"
            query = ""

        def __init__(self) -> None:
            self.headers = {}

        async def body(self) -> bytes:
            return b'{"model":"gpt-5.6-terra"}'

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        async def respond(self, authorization: str, *, stream: bool = False) -> httpx.Response:
            upstream_authorizations.append(authorization)
            if authorization == f"Bearer {old_access}":
                status = 401
                content = b'{"error":{"code":"token_revoked"}}'
            else:
                status = 200
                content = b'{"ok":true}'
            if stream:
                return httpx.Response(status, stream=FakeStream(content))
            return httpx.Response(status, content=content)

        async def request(self, **kwargs):
            return await self.respond(kwargs["headers"]["authorization"])

        def build_request(self, *, method, url, content, headers):
            return httpx.Request(method, url, content=content, headers=headers)

        async def send(self, request, *, stream):
            assert stream is True
            return await self.respond(request.headers["authorization"], stream=True)

        async def aclose(self) -> None:
            return None

    def fake_post(url, *, data, timeout):
        nonlocal refresh_calls
        del url, timeout
        refresh_calls += 1
        assert data["refresh_token"] == "refresh-token"
        return httpx.Response(
            200,
            json={
                "access_token": new_access,
                "refresh_token": "rotated-refresh-token",
            },
        )

    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr("codex_cockpit_lite.auth.httpx.post", fake_post)

    response = await proxy_module._proxy_with_retry(
        FakeRequest(),
        "https://chatgpt.com/backend-api/codex/responses",
        tmp_path,
        is_sse=is_sse,
        max_retries=0,
    )
    saved = json.loads((account_path / "auth.json").read_text())

    assert response.status_code == 200
    if is_sse:
        assert b"".join([chunk async for chunk in response.body_iterator]) == b'{"ok":true}'
    else:
        assert response.body == b'{"ok":true}'
    assert upstream_authorizations == [f"Bearer {old_access}", f"Bearer {new_access}"]
    assert refresh_calls == 1
    assert saved["tokens"]["access_token"] == new_access
    assert saved["tokens"]["refresh_token"] == "rotated-refresh-token"


@pytest.mark.asyncio
async def test_401_refresh_rejection_preserves_auth_and_original_upstream_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account_id = "account-1"
    account_path = tmp_path / "accounts" / account_id
    account_path.mkdir(parents=True)
    raw = chatgpt_auth()
    original_json = json.dumps(raw)
    (account_path / "auth.json").write_text(original_json)
    save_meta(
        AccountMeta(
            id=account_id,
            email="test@example.com",
            quota=QuotaSnapshot(weekly_percent=50, hourly_percent=50),
        ),
        tmp_path,
    )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = [account_id]
    save_config(cfg, tmp_path)
    activate_account(account_id, tmp_path)
    upstream_calls = 0

    class FakeRequest:
        method = "POST"

        class url:
            path = "/v1/responses"
            query = ""

        def __init__(self) -> None:
            self.headers = {}

        async def body(self) -> bytes:
            return b"{}"

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        async def request(self, **kwargs):
            nonlocal upstream_calls
            del kwargs
            upstream_calls += 1
            return httpx.Response(
                401,
                content=b'{"error":{"code":"token_revoked"}}',
            )

        async def aclose(self) -> None:
            return None

    def fake_post(url, *, data, timeout):
        del url, data, timeout
        return httpx.Response(
            400,
            json={"error": {"code": "invalid_grant", "message": "Login required"}},
        )

    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr("codex_cockpit_lite.auth.httpx.post", fake_post)

    response = await proxy_module._proxy_with_retry(
        FakeRequest(),
        "https://chatgpt.com/backend-api/codex/responses",
        tmp_path,
        max_retries=0,
    )

    assert response.status_code == 401
    assert response.body == b'{"error":{"code":"token_revoked"}}'
    assert upstream_calls == 1
    assert json.loads((account_path / "auth.json").read_text()) == json.loads(original_json)
    invalidated = load_meta(account_id, tmp_path)
    assert invalidated is not None
    assert invalidated.requires_reauth is True
    assert invalidated.quota.weekly_percent is None
    assert invalidated.quota.hourly_percent is None
    assert invalidated.quota.weekly_resets_at is None
    assert invalidated.quota.hourly_resets_at is None


@pytest.mark.asyncio
async def test_quota_failure_clears_cached_percentages_and_dates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    account_id = "account-1"
    account_path = tmp_path / "accounts" / account_id
    account_path.mkdir(parents=True)
    (account_path / "auth.json").write_text(json.dumps(chatgpt_auth()))
    save_meta(
        AccountMeta(
            id=account_id,
            email="test@example.com",
            quota=QuotaSnapshot(
                weekly_percent=100,
                hourly_percent=100,
                weekly_resets_at=123,
                hourly_resets_at=456,
                queried_at=789,
            ),
        ),
        tmp_path,
    )

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def get(self, url, *, headers):
            del url, headers
            return httpx.Response(503, request=httpx.Request("GET", "https://example.test"))

    monkeypatch.setattr(quota_module.httpx, "AsyncClient", FakeClient)

    result = await quota_module.refresh_quota(account_id, tmp_path)
    saved = load_meta(account_id, tmp_path)

    assert result == QuotaSnapshot()
    assert saved is not None
    assert saved.requires_reauth is False
    assert saved.quota == QuotaSnapshot()


@pytest.mark.parametrize(
    ("refresh_error_code", "refresh_error_message"),
    [
        (
            "refresh_token_invalidated",
            "Your session has ended. Please log in again.",
        ),
        (
            "refresh_token_reused",
            "Your refresh token has already been used to generate a new access token. "
            "Please try signing in again.",
        ),
    ],
)
@pytest.mark.asyncio
async def test_quota_401_with_invalid_refresh_marks_account_for_reauth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    refresh_error_code: str,
    refresh_error_message: str,
) -> None:
    account_id = "account-1"
    account_path = tmp_path / "accounts" / account_id
    account_path.mkdir(parents=True)
    (account_path / "auth.json").write_text(json.dumps(chatgpt_auth()))
    save_meta(
        AccountMeta(
            id=account_id,
            email="test@example.com",
            quota=QuotaSnapshot(weekly_percent=100, hourly_percent=100, queried_at=1),
        ),
        tmp_path,
    )

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            del args

        async def get(self, url, *, headers):
            del url, headers
            return httpx.Response(401, request=httpx.Request("GET", "https://example.test"))

    def rejected_refresh(url, *, data, timeout):
        del url, data, timeout
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": refresh_error_code,
                    "message": refresh_error_message,
                }
            },
        )

    monkeypatch.setattr(quota_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr("codex_cockpit_lite.auth.httpx.post", rejected_refresh)

    await quota_module.refresh_quota(account_id, tmp_path)
    saved = load_meta(account_id, tmp_path)

    assert saved is not None
    assert saved.requires_reauth is True
    assert refresh_error_code in saved.reauth_reason
    assert saved.quota == QuotaSnapshot()


@pytest.mark.parametrize(
    ("proxy_name", "expected_path"),
    [
        ("proxy_images_generations", "images/generations"),
        ("proxy_images_edits", "images/edits"),
    ],
)
@pytest.mark.asyncio
async def test_images_use_chatgpt_codex_upstream(
    monkeypatch: pytest.MonkeyPatch, proxy_name: str, expected_path: str
) -> None:
    captured: dict[str, object] = {}

    async def fake_proxy(request, upstream_url, config_dir=None, is_sse=False, max_retries=8):
        del request, config_dir, max_retries
        captured.update(upstream_url=upstream_url, is_sse=is_sse)
        return main_module.Response(status_code=204)

    monkeypatch.setattr(proxy_module, "_proxy_with_retry", fake_proxy)

    response = await getattr(proxy_module, proxy_name)(object())

    assert response.status_code == 204
    assert captured == {
        "upstream_url": f"https://chatgpt.com/backend-api/codex/{expected_path}",
        "is_sse": False,
    }


@pytest.mark.asyncio
async def test_429_refreshes_all_accounts_then_retries_with_next_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for account_id in ["first", "second"]:
        save_meta(
            AccountMeta(
                id=account_id,
                email=f"{account_id}@example.com",
                quota=QuotaSnapshot(weekly_percent=50, hourly_percent=50),
            ),
            tmp_path,
        )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = ["first", "second"]
    save_config(cfg, tmp_path)
    activate_account("first", tmp_path)
    auth_accounts: list[str] = []
    refresh_order: list[str] = []

    class FakeRequest:
        method = "POST"

        class url:
            path = "/v1/responses"
            query = ""

        def __init__(self) -> None:
            self.headers = {}

        async def body(self) -> bytes:
            return b'{"model":"gpt-5.6-terra"}'

    class FakeClient:
        response_statuses = iter([429, 200])

        def __init__(self, **kwargs):
            del kwargs

        async def request(self, **kwargs):
            del kwargs
            return httpx.Response(next(self.response_statuses), content=b'{"ok":true}')

        async def aclose(self) -> None:
            return None

    def fake_auth(account_id: str, config_dir=None) -> dict[str, str]:
        assert config_dir == tmp_path
        auth_accounts.append(account_id)
        return {"Authorization": f"Bearer {account_id}"}

    async def fake_refresh(account_id: str, config_dir=None) -> QuotaSnapshot:
        assert config_dir == tmp_path
        refresh_order.append(account_id)
        if account_id == "first":
            return QuotaSnapshot(weekly_percent=0, hourly_percent=50, queried_at=1)
        return QuotaSnapshot(weekly_percent=50, hourly_percent=50, queried_at=1)

    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(proxy_module, "build_auth_headers", fake_auth)
    monkeypatch.setattr(proxy_module, "refresh_quota", fake_refresh)

    response = await proxy_module._proxy_with_retry(
        FakeRequest(),
        "https://chatgpt.com/backend-api/codex/responses",
        tmp_path,
        auth_header_builder=fake_auth,
    )

    assert response.status_code == 200
    assert auth_accounts == ["first", "second"]
    assert refresh_order == ["first", "second"]


@pytest.mark.asyncio
async def test_429_returns_immediately_when_refreshed_accounts_are_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    save_meta(
        AccountMeta(
            id="only",
            email="only@example.com",
            quota=QuotaSnapshot(weekly_percent=50, hourly_percent=50),
        ),
        tmp_path,
    )
    cfg = load_config(tmp_path)
    cfg.api.selected_accounts = ["only"]
    save_config(cfg, tmp_path)
    activate_account("only", tmp_path)
    upstream_calls = 0

    class FakeRequest:
        method = "POST"

        class url:
            path = "/v1/responses"
            query = ""

        def __init__(self) -> None:
            self.headers = {}

        async def body(self) -> bytes:
            return b"{}"

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        async def request(self, **kwargs):
            nonlocal upstream_calls
            del kwargs
            upstream_calls += 1
            return httpx.Response(429, content=b'{"error":"quota"}')

        async def aclose(self) -> None:
            return None

    async def fake_refresh(account_id: str, config_dir=None) -> QuotaSnapshot:
        assert account_id == "only"
        assert config_dir == tmp_path
        return QuotaSnapshot(weekly_percent=0, hourly_percent=0, queried_at=1)

    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", FakeClient)

    def fake_auth(account_id: str, config_dir=None) -> dict[str, str]:
        assert account_id == "only"
        assert config_dir == tmp_path
        return {"Authorization": "Bearer test"}

    monkeypatch.setattr(proxy_module, "refresh_quota", fake_refresh)

    response = await proxy_module._proxy_with_retry(
        FakeRequest(),
        "https://chatgpt.com/backend-api/codex/responses",
        tmp_path,
        auth_header_builder=fake_auth,
    )

    assert response.status_code == 429
    assert json.loads(response.body) == {"error": "All accounts exhausted"}
    assert upstream_calls == 1


@pytest.mark.asyncio
async def test_alpha_search_drops_stale_decoded_encoding_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b'{"output":"ok"}'

    class FakeRequest:
        method = "POST"

        class url:
            path = "/v1/alpha/search"
            query = "must=not-forward"

        def __init__(self) -> None:
            self.headers = {"X-Client-Only": "must-not-forward"}

        async def body(self) -> bytes:
            return b'{"model":"gpt-5.6-terra","commands":{}}'

    class FakeClient:
        def __init__(self, **kwargs):
            del kwargs

        async def request(self, *, method, url, content, headers):
            assert method == "POST"
            assert url == "https://chatgpt.com/backend-api/codex/alpha/search"
            assert content == b'{"model":"gpt-5.6-terra","commands":{}}'
            assert headers["content-type"] == "application/json"
            assert headers["authorization"] == "Bearer test"
            assert "x-client-only" not in headers
            assert "originator" not in headers
            assert "service_tier" not in headers
            return httpx.Response(
                200,
                content=gzip.compress(content_bytes),
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": "123",
                    "X-Request-Id": "search-request-1",
                },
            )

        async def aclose(self) -> None:
            return None

    content_bytes = content
    monkeypatch.setattr(
        proxy_module,
        "get_active_account",
        lambda config_dir=None: {
            "id": "account-1",
            "email": "test@example.com",
        },
    )
    monkeypatch.setattr(
        proxy_module,
        "build_search_headers",
        lambda account_id, config_dir=None: {"Authorization": "Bearer test"},
    )
    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", FakeClient)

    response = await proxy_module.proxy_alpha_search(FakeRequest(), tmp_path)

    assert response.status_code == 200
    assert response.body == content
    assert response.headers.get("content-encoding") is None
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["x-request-id"] == "search-request-1"


@pytest.mark.asyncio
async def test_port_protocol_is_announced_after_uvicorn_listener_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def fake_startup(
        server: uvicorn.Server, sockets: list[socket.socket] | None = None
    ) -> None:
        del sockets
        events.append("listener-ready")
        server.started = True

    def fake_print(value: str, *, flush: bool = False) -> None:
        assert flush is True
        events.append(value)

    monkeypatch.setattr(uvicorn.Server, "startup", fake_startup)
    monkeypatch.setattr("builtins.print", fake_print)
    monkeypatch.setattr(main_module, "_actual_port", 18844)
    monkeypatch.setattr(main_module, "_shutdown_token", "test-control-token")
    server = main_module.CockpitServer(uvicorn.Config(app, port=18844))

    await server.startup()

    assert events == [
        "listener-ready",
        "CONTROL=test-control-token",
        "PORT=18844",
    ]


@pytest.mark.asyncio
async def test_shutdown_endpoint_requires_exact_control_token() -> None:
    class FakeServer:
        should_exit = False

    fake_server = FakeServer()
    app.state.backend_server = fake_server
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post("/api/cockpit/shutdown")
        invalid = await client.post(
            "/api/cockpit/shutdown",
            headers={"X-Codex-Cockpit-Control": "wrong-token"},
        )

    assert missing.status_code == 404
    assert invalid.status_code == 404
    assert fake_server.should_exit is False


@pytest.mark.asyncio
async def test_shutdown_endpoint_stops_server_with_exact_control_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeServer:
        should_exit = False

    fake_server = FakeServer()
    app.state.backend_server = fake_server
    monkeypatch.setattr(main_module, "_shutdown_token", "test-control-token")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/cockpit/shutdown",
            headers={"X-Codex-Cockpit-Control": "test-control-token"},
        )

    assert response.status_code == 204
    assert fake_server.should_exit is True


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
async def test_account_display_name_can_be_set_and_cleared(tmp_path: Path) -> None:
    save_meta(AccountMeta(id="account-1", name="Automatic Team"), tmp_path)
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            renamed = await client.put(
                "/api/accounts/account-1/display-name",
                json={"display_name": "  My Workspace  "},
            )
            cleared = await client.put(
                "/api/accounts/account-1/display-name", json={"display_name": ""}
            )

        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Automatic Team"
        assert renamed.json()["display_name"] == "My Workspace"
        assert cleared.status_code == 200
        assert cleared.json()["name"] == "Automatic Team"
        assert cleared.json()["display_name"] == ""
        assert load_meta("account-1", tmp_path).display_name == ""
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.parametrize(
    ("display_name", "expected_display_name"),
    [("", ""), ("  Custom Workspace  ", "Custom Workspace")],
)
@pytest.mark.asyncio
async def test_manual_import_separates_automatic_and_display_names(
    tmp_path: Path, display_name: str, expected_display_name: str
) -> None:
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/accounts/import",
                json={
                    "auth_json": json.dumps(chatgpt_auth()),
                    "name": display_name,
                },
            )

        assert response.status_code == 200
        assert response.json()["name"] == "test"
        assert response.json()["display_name"] == expected_display_name
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_browser_login_replaces_matching_account_and_clears_reauth(
    tmp_path: Path,
) -> None:
    existing_id = "existing-account"
    save_meta(
        AccountMeta(
            id=existing_id,
            name="Old Name",
            display_name="SCSC",
            email="test@example.com",
            account_id="account-1",
            requires_reauth=True,
            reauth_reason="expired",
            quota=QuotaSnapshot(weekly_percent=100, hourly_percent=100, queried_at=1),
        ),
        tmp_path,
    )
    set_api_config_dir(tmp_path)
    replacement = chatgpt_auth()
    replacement["tokens"]["refresh_token"] = "replacement-refresh-token"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/accounts/browser-login",
                json={"auth_json": replacement, "reauth_account_id": None},
            )

        assert response.status_code == 200
        assert response.json()["id"] == existing_id
        assert response.json()["display_name"] == "SCSC"
        assert response.json()["requires_reauth"] is False
        assert response.json()["quota"] == QuotaSnapshot().model_dump()
        saved_auth = json.loads((tmp_path / "accounts" / existing_id / "auth.json").read_text())
        assert saved_auth["tokens"]["refresh_token"] == "replacement-refresh-token"
        assert len(list((tmp_path / "accounts").iterdir())) == 1
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_reauth_with_different_identity_adds_account_and_keeps_old_invalid(
    tmp_path: Path,
) -> None:
    old_id = "old-account"
    save_meta(
        AccountMeta(
            id=old_id,
            email="old@example.com",
            account_id="old-chatgpt-account",
            requires_reauth=True,
            reauth_reason="expired",
        ),
        tmp_path,
    )
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/accounts/browser-login",
                json={
                    "auth_json": chatgpt_auth("new@example.com", "new-chatgpt-account"),
                    "reauth_account_id": old_id,
                },
            )

        assert response.status_code == 200
        new_id = response.json()["id"]
        assert new_id != old_id
        assert response.json()["email"] == "new@example.com"
        old = load_meta(old_id, tmp_path)
        assert old is not None
        assert old.requires_reauth is True
        assert old.reauth_reason == "expired"
        assert len(list((tmp_path / "accounts").iterdir())) == 2
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_import_from_codex_empty_body_detects_duplicate_and_force_reuses_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "config"
    home_dir = tmp_path / "home"
    auth_path = home_dir / ".codex" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps(chatgpt_auth()))
    save_meta(
        AccountMeta(
            id="existing-account",
            name="Existing Account",
            email="test@example.com",
            account_id="account-1",
        ),
        config_dir,
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))
    set_api_config_dir(config_dir)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            duplicate = await client.post(
                "/api/accounts/import-from-codex",
                content=b"",
                headers={"content-type": "application/json"},
            )
            overwritten = await client.post("/api/accounts/import-from-codex", json={"force": True})

        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "DUPLICATE: existing-account"
        assert overwritten.status_code == 200
        assert overwritten.json()["id"] == "existing-account"
        assert len(list((config_dir / "accounts").iterdir())) == 1
        assert (config_dir / "accounts" / "existing-account" / "auth.json").exists()
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_import_from_codex_rejects_malformed_nonempty_body(tmp_path: Path) -> None:
    set_api_config_dir(tmp_path)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/accounts/import-from-codex",
                content=b"not-json",
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 400
        assert response.json()["detail"].startswith("Invalid request JSON:")
    finally:
        set_api_config_dir(get_config_dir())


@pytest.mark.asyncio
async def test_lifespan_cancels_quota_refresh_task() -> None:
    async with app.router.lifespan_context(app):
        task = app.state.quota_refresh_task
        assert task.done() is False
    assert task.cancelled() is True
