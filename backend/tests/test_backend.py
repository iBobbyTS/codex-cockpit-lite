"""Tests for Codex Cockpit Lite backend."""

import json
import os
import tempfile
from pathlib import Path

import pytest
from models import AppConfig, ApiConfig, AccountMeta, QuotaSnapshot, AuthMode, SpeedMode
from config import (
    load_config, save_config, ensure_config_dir,
    load_meta, save_meta, list_account_metas, list_selected_accounts,
)
from auth import parse_auth_file, get_auth_mode, _token_expired


# ─── Config tests ───

def test_default_config():
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        cfg = load_config(config_dir)
        assert cfg.version == 1
        assert cfg.api.port == 8844
        assert cfg.api.speed == SpeedMode.STANDARD
        assert cfg.api.selected_accounts == []
        assert cfg.api.auto_switch.enabled is True


def test_save_and_reload_config():
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        cfg = load_config(config_dir)
        cfg.api.port = 9999
        cfg.api.speed = SpeedMode.FAST
        save_config(cfg, config_dir)

        reloaded = load_config(config_dir)
        assert reloaded.api.port == 9999
        assert reloaded.api.speed == SpeedMode.FAST


def test_config_dir_endpoint_reports_backend_path():
    from api import get_config_dir_info, set_api_config_dir
    from config import get_config_dir

    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        set_api_config_dir(config_dir)
        try:
            result = get_config_dir_info()
            assert result == {"path": str(config_dir)}
        finally:
            set_api_config_dir(get_config_dir())


def test_account_meta_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        meta = AccountMeta(
            id="test-1",
            name="Test Account",
            email="test@openai.com",
            auth_mode=AuthMode.OAUTH,
            plan_type="pro",
            team_name="Personal",
            quota=QuotaSnapshot(weekly_percent=80, hourly_percent=30),
        )
        save_meta(meta, config_dir)
        loaded = load_meta("test-1", config_dir)
        assert loaded is not None
        assert loaded.id == "test-1"
        assert loaded.email == "test@openai.com"
        assert loaded.quota.weekly_percent == 80


def test_list_selected_accounts():
    with tempfile.TemporaryDirectory() as tmp:
        config_dir = Path(tmp)
        # Create two accounts
        for i in range(2):
            meta = AccountMeta(
                id=f"acc-{i}",
                name=f"Account {i}",
                email=f"user{i}@openai.com",
                auth_mode=AuthMode.OAUTH,
            )
            save_meta(meta, config_dir)

        # Add only first to selected
        cfg = load_config(config_dir)
        cfg.api.selected_accounts = ["acc-0"]
        save_config(cfg, config_dir)

        selected = list_selected_accounts(config_dir)
        assert len(selected) == 1
        assert selected[0].id == "acc-0"


# ─── Auth tests ───

def test_parse_oauth_auth_file():
    raw = {
        "tokens": {
            "id_token": "eyJ...",
            "access_token": "eyJ...access",
            "refresh_token": "rt_...",
        },
        "OPENAI_API_KEY": None,
    }
    auth = parse_auth_file(raw)
    assert get_auth_mode(auth) == AuthMode.OAUTH
    assert auth.tokens is not None
    assert auth.tokens.access_token == "eyJ...access"


def test_parse_apikey_auth_file():
    raw = {
        "auth_mode": "apikey",
        "OPENAI_API_KEY": "sk-test123",
    }
    auth = parse_auth_file(raw)
    assert get_auth_mode(auth) == AuthMode.API_KEY


def test_parse_agent_identity_file():
    raw = {
        "auth_mode": "agentIdentity",
        "agent_identity": {
            "agent_runtime_id": "rt_123",
            "agent_private_key": "base64key...",
            "task_id": "task_1",
            "account_id": "acc_1",
            "chatgpt_user_id": "user_1",
        },
    }
    auth = parse_auth_file(raw)
    assert get_auth_mode(auth) == AuthMode.AGENT_IDENTITY


def test_token_not_expired():
    # Create a token that expires in 1 hour
    import time, jwt
    payload = {"exp": int(time.time()) + 3600}
    token = jwt.encode(payload, "secret", algorithm="HS256")
    assert _token_expired(token) is False


def test_token_expired():
    import time, jwt
    payload = {"exp": int(time.time()) - 100}
    token = jwt.encode(payload, "secret", algorithm="HS256")
    assert _token_expired(token) is True


# ─── Models tests ───

def test_app_config_serialization():
    cfg = AppConfig(api=ApiConfig(port=1456, speed=SpeedMode.FAST))
    data = cfg.model_dump()
    assert data["api"]["port"] == 1456
    reloaded = AppConfig(**data)
    assert reloaded.api.speed == SpeedMode.FAST


def test_cockpit_status_model():
    from models import CockpitStatus
    status = CockpitStatus(
        running=True,
        uptime_seconds=10.5,
        accounts=[],
        recent_requests=[],
    )
    d = status.model_dump()
    assert d["running"] is True
    assert d["uptime_seconds"] == 10.5


# ─── Quota parsing tests ───

def test_parse_timestamp():
    from quota import _parse_timestamp
    import time

    # Unix timestamp in seconds
    ts = int(time.time()) + 86400
    assert _parse_timestamp(str(ts)) == ts

    # Unix timestamp in milliseconds
    ms = str(ts * 1000)
    assert _parse_timestamp(ms) == ts

    # ISO format
    assert _parse_timestamp("2026-12-31T00:00:00Z") > 0


def test_collect_records():
    from quota import _collect_records

    data = {
        "accounts": {
            "default": {
                "account": {
                    "plan_type": "pro",
                    "account_id": "acc_1",
                }
            }
        }
    }
    records = _collect_records(data)
    assert len(records) == 1
    assert records[0]["account"]["plan_type"] == "pro"


def test_collect_records_array():
    from quota import _collect_records

    data = {
        "accounts": [
            {"account": {"plan_type": "team"}},
            {"account": {"plan_type": "pro"}},
        ]
    }
    records = _collect_records(data)
    assert len(records) == 2


def test_find_available_port():
    from main import find_available_port

    # Default port should be available in test environment
    port = find_available_port(8844)
    assert port >= 8844
    assert port < 9000


def test_find_available_port_skips_occupied():
    import socket
    from main import find_available_port

    # Occupy port 18844
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 18844))
    sock.listen(1)

    try:
        port = find_available_port(18844)
        assert port == 18845  # Should skip to next
    finally:
        sock.close()
