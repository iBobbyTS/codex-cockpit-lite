"""Single source of truth for account authentication availability."""

from __future__ import annotations

from pathlib import Path

from .config import load_meta, save_meta
from .models import AccountMeta, QuotaSnapshot


def clear_quota(account_id: str, config_dir: Path | None = None) -> AccountMeta | None:
    """Persist an unavailable quota without misrepresenting it as zero remaining."""
    meta = load_meta(account_id, config_dir)
    if meta is None:
        return None
    meta.quota = QuotaSnapshot()
    save_meta(meta, config_dir)
    return meta


def mark_requires_reauth(
    account_id: str,
    reason: str,
    config_dir: Path | None = None,
) -> AccountMeta | None:
    """Make an invalid token chain unschedulable and clear stale quota data."""
    meta = load_meta(account_id, config_dir)
    if meta is None:
        return None
    meta.requires_reauth = True
    meta.reauth_reason = reason.strip() or "登录已失效, 请重新登录"
    meta.quota = QuotaSnapshot()
    save_meta(meta, config_dir)
    return meta


def clear_requires_reauth(meta: AccountMeta) -> AccountMeta:
    meta.requires_reauth = False
    meta.reauth_reason = ""
    return meta
