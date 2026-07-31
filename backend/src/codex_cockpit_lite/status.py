"""Status endpoint — exposes backend state to the frontend."""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter

from .config import list_account_metas, load_config
from .models import CockpitStatus, ProxyRequestLog
from .proxy import (
    get_active_account,
    get_active_index,
    get_recent_requests,
    get_request_count,
)

router = APIRouter()
_start_time = time.time()
_config_dir: Path | None = None
_actual_port: int = 0
_bind_host: str = "127.0.0.1"
_LOOPBACK_ADDRESS = "127.0.0.1"


def set_config_dir(path: Path) -> None:
    global _config_dir
    _config_dir = path


def set_actual_port(port: int) -> None:
    global _actual_port
    _actual_port = port


def set_bind_host(host: str) -> None:
    global _bind_host
    _bind_host = host


def _active_ipv4_candidates() -> Iterator[str]:
    """Yield the default-route address first, then hostname interface addresses."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            yield str(sock.getsockname()[0])
    except OSError:
        pass

    try:
        for address_info in socket.getaddrinfo(
            socket.gethostname(),
            None,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        ):
            yield str(address_info[4][0])
    except OSError:
        pass


def find_active_lan_address() -> str:
    """Return the first usable IPv4 address, falling back to loopback."""
    for candidate in _active_ipv4_candidates():
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if (
            address.version == 4
            and not address.is_loopback
            and not address.is_link_local
            and not address.is_unspecified
        ):
            return str(address)
    return _LOOPBACK_ADDRESS


def build_service_url(bind_host: str, port: int) -> str:
    host = _LOOPBACK_ADDRESS if bind_host == _LOOPBACK_ADDRESS else find_active_lan_address()
    return f"http://{host}:{port}/v1"


@router.get("/v1/cockpit/status")
async def get_status() -> CockpitStatus:
    cfg = load_config(_config_dir)
    active = get_active_account(_config_dir)
    accounts = list_account_metas(_config_dir)
    selected_ids = set(cfg.api.selected_accounts)

    recent = []
    for r in get_recent_requests()[-20:]:
        recent.append(
            ProxyRequestLog(
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
            )
        )

    # Mark selected accounts
    for meta in accounts:
        meta.enabled = meta.id in selected_ids

    return CockpitStatus(
        running=True,
        uptime_seconds=time.time() - _start_time,
        actual_port=_actual_port,
        service_url=build_service_url(_bind_host, _actual_port),
        active_account_index=get_active_index(),
        active_account_id=active["id"] if active else "",
        active_account_email=active["email"] if active else "",
        total_requests=get_request_count(),
        accounts=accounts,
        recent_requests=list(reversed(recent)),
    )
