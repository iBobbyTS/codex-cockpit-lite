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
import codex_cockpit_lite.status as status_module
from codex_cockpit_lite.api import get_config_dir_info, set_api_config_dir
from codex_cockpit_lite.auth import (
    _token_expired,
    build_auth_headers,
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
    assert cfg.api.password == "sandrone"
    assert cfg.api.account_order == []
    assert cfg.api.selected_accounts == []
    assert cfg.api.auto_switch.enabled is True


def test_save_and_reload_config(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    cfg.api.port = 9999
    cfg.api.speed = SpeedMode.FAST
    cfg.api.password = "changed-password"
    save_config(cfg, tmp_path)

    reloaded = load_config(tmp_path)
    assert reloaded.api.port == 9999
    assert reloaded.api.speed == SpeedMode.FAST
    assert reloaded.api.password == "changed-password"


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
async def test_models_api_route_precedes_spa_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_proxy_models(request, config_dir):
        del request, config_dir
        return main_module.JSONResponse({"object": "list", "data": []})

    monkeypatch.setattr(proxy_module, "proxy_models", fake_proxy_models)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}


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
async def test_alpha_search_drops_stale_decoded_encoding_headers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    content = b'{"output":"ok"}'

    class FakeRequest:
        async def body(self) -> bytes:
            return b'{"model":"gpt-5.6-terra","commands":{}}'

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            del exc_type, exc, traceback

        async def post(self, url, *, content, headers):
            assert url == "https://chatgpt.com/backend-api/codex/alpha/search"
            assert content == b'{"model":"gpt-5.6-terra","commands":{}}'
            assert headers["Content-Type"] == "application/json"
            return httpx.Response(
                200,
                content=gzip.compress(content_bytes),
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": "123",
                    "X-Request-Id": "search-request-1",
                },
            )

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
    monkeypatch.setattr(proxy_module.httpx, "AsyncClient", lambda **kwargs: FakeClient())

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
