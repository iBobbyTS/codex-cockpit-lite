"""Data models for Codex Cockpit Lite."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AuthMode(StrEnum):
    OAUTH = "oauth"


class SpeedMode(StrEnum):
    STANDARD = "standard"
    FAST = "fast"


class AutoSwitchStrategy(StrEnum):
    SEQUENTIAL = "sequential"


class ApiConfig(BaseModel):
    port: int = 8844
    bind_host: str = "127.0.0.1"
    speed: SpeedMode = SpeedMode.STANDARD
    selected_accounts: list[str] = Field(default_factory=list)
    auto_switch: AutoSwitchConfig = Field(default_factory=lambda: AutoSwitchConfig())


class AutoSwitchConfig(BaseModel):
    enabled: bool = True
    strategy: AutoSwitchStrategy = AutoSwitchStrategy.SEQUENTIAL
    quota_threshold_percent: int = 95


class AppConfig(BaseModel):
    version: int = 1
    api: ApiConfig = Field(default_factory=ApiConfig)


class QuotaSnapshot(BaseModel):
    weekly_percent: int = 0
    hourly_percent: int = 0
    weekly_resets_at: int | None = None
    hourly_resets_at: int | None = None
    queried_at: int = 0


class AccountMeta(BaseModel):
    id: str
    name: str = ""
    display_name: str = ""
    email: str = ""
    auth_mode: AuthMode = AuthMode.OAUTH
    plan_type: str = ""
    subscription_expires_at: int | None = None
    team_name: str = ""
    account_id: str = ""
    quota: QuotaSnapshot = Field(default_factory=QuotaSnapshot)
    enabled: bool = True
    speed: SpeedMode = SpeedMode.STANDARD


class AuthFile(BaseModel):
    """Official Codex ~/.codex/auth.json format."""

    model_config = ConfigDict(populate_by_name=True)

    auth_mode: str | None = None
    tokens: AuthTokens | None = None


class AuthTokens(BaseModel):
    id_token: str = ""
    access_token: str
    refresh_token: str | None = None
    account_id: str | None = None


class ProxyRequestLog(BaseModel):
    id: str
    timestamp: float
    account_id: str
    account_email: str
    method: str
    path: str
    model: str
    status: int
    duration_ms: int
    error: str | None = None


class CockpitStatus(BaseModel):
    running: bool
    version: str = "0.1.0"
    uptime_seconds: float = 0
    actual_port: int = 0
    active_account_index: int = 0
    active_account_id: str = ""
    active_account_email: str = ""
    total_requests: int = 0
    accounts: list[AccountMeta] = Field(default_factory=list)
    recent_requests: list[ProxyRequestLog] = Field(default_factory=list)
    backend_error: str | None = None
