"""Status endpoint — exposes backend state to the frontend."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter

from config import list_account_metas, load_config
from models import CockpitStatus, ProxyRequestLog
from proxy import (
    get_active_account, get_request_count, get_recent_requests, get_active_index,
)

router = APIRouter()
_start_time = time.time()
_config_dir: Optional[Path] = None
_actual_port: int = 0


def set_config_dir(path: Path) -> None:
    global _config_dir
    _config_dir = path


def set_actual_port(port: int) -> None:
    global _actual_port
    _actual_port = port


@router.get("/v1/cockpit/status")
async def get_status() -> CockpitStatus:
    cfg = load_config(_config_dir)
    active = get_active_account(_config_dir)
    accounts = list_account_metas(_config_dir)
    selected_ids = set(cfg.api.selected_accounts)

    recent = []
    for r in get_recent_requests()[-20:]:
        recent.append(ProxyRequestLog(
            id=r["id"],
            timestamp=r["timestamp"],
            account_id=r["account_id"],
            account_email=r["account_email"],
            method=r["method"],
            path=r["path"],
            model=r.get("model", ""),
            status=r["status"],
            duration_ms=r["duration_ms"],
            error=r.get("error"),
        ))

    # Mark selected accounts
    for meta in accounts:
        meta.enabled = meta.id in selected_ids

    return CockpitStatus(
        running=True,
        uptime_seconds=time.time() - _start_time,
        actual_port=_actual_port,
        active_account_index=get_active_index(),
        active_account_id=active["id"] if active else "",
        active_account_email=active["email"] if active else "",
        total_requests=get_request_count(),
        accounts=accounts,
        recent_requests=list(reversed(recent)),
    )
