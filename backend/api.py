"""Management API — config, accounts, CRUD. Frontend talks to this, not Tauri."""

from __future__ import annotations

import json
import uuid
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config import (
    load_config, save_config, ensure_config_dir, get_config_dir,
    load_meta, save_meta, list_account_metas, list_selected_accounts,
    load_auth_file, account_dir,
)
from models import (
    AppConfig, AccountMeta, QuotaSnapshot, AuthMode,
)
from quota import refresh_quota as py_refresh_quota
from quota import refresh_subscription as py_refresh_subscription

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
_config_dir: Optional[Path] = None


def set_api_config_dir(path: Path) -> None:
    global _config_dir
    _config_dir = path


def _cd() -> Path:
    return _config_dir or get_config_dir()


def _extract_account_id(auth_data: dict) -> str:
    """Extract account_id from tokens.account_id in auth.json."""
    return (auth_data.get("tokens") or {}).get("account_id", "") or ""


def _find_account_by_email(email: str):
    if not email:
        return None
    for meta in list_account_metas(_cd()):
        if meta.email.lower() == email.lower():
            return meta
    return None


def _dedup_account(email: str, account_id: str):
    """Same email + same account_id = update. Same email + diff id = new."""
    if not email:
        return None
    for meta in list_account_metas(_cd()):
        if meta.email.lower() != email.lower():
            continue
        # Match if stored account has no account_id, or account_id matches
        if not meta.account_id or meta.account_id == account_id:
            return meta
    return None


# ─── Config ───

@router.get("/config")
async def get_config():
    return load_config(_cd()).model_dump()


@router.put("/config")
async def put_config(body: dict):
    try:
        cfg = AppConfig(**body)
    except Exception as e:
        raise HTTPException(400, f"Invalid config: {e}")
    save_config(cfg, _cd())
    return {"ok": True}


# ─── Accounts ───

@router.get("/accounts")
async def get_accounts():
    metas = list_account_metas(_cd())
    cfg = load_config(_cd())
    selected = set(cfg.api.selected_accounts)
    for m in metas:
        m.enabled = m.id in selected
    return [m.model_dump() for m in metas]


@router.post("/accounts/import")
async def import_account(req: Request):
    body = await req.json()
    auth_json = body.get("auth_json", "")
    name = body.get("name", "")

    if not auth_json.strip():
        raise HTTPException(400, "auth_json is required")

    # Validate JSON
    try:
        auth_data = json.loads(auth_json)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid auth.json: {e}")

    account_id = str(uuid.uuid4())
    ad = account_dir(account_id, _cd())
    ad.mkdir(parents=True, exist_ok=True)

    # Check auth mode BEFORE writing files
    if (auth_data.get("auth_mode") or "").lower() != "chatgpt":
        raise HTTPException(400, "UNSUPPORTED_AUTH: Codex Cockpit Lite 只支持 ChatGPT (OAuth) 登录")

    # Extract email for dedup
    email = ""
    if "tokens" in auth_data and "id_token" in auth_data["tokens"]:
        import jwt
        try:
            payload = jwt.decode(auth_data["tokens"]["id_token"], options={"verify_signature": False})
            email = payload.get("email", "")
        except Exception:
            pass
    if not email and "agent_identity" in auth_data:
        email = auth_data["agent_identity"].get("email", "")

    if not name:
        name = email.split("@")[0] if email else "Codex Account"

    # Extract account_id from JWT for team-aware dedup
    chatgpt_account_id = _extract_account_id(auth_data)

    force = body.get("force", False)
    existing = _dedup_account(email, chatgpt_account_id or "")
    if existing and not force:
        raise HTTPException(409, f"DUPLICATE: {existing.id}")
    account_id = existing.id if existing else str(uuid.uuid4())

    ad = account_dir(account_id, _cd())
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "auth.json").write_text(json.dumps(auth_data, indent=2))

    meta = AccountMeta(
        id=account_id,
        name=name,
        email=email,
        auth_mode=AuthMode("oauth"),
        account_id=chatgpt_account_id or "",
    )
    save_meta(meta, _cd())

    # Auto-add to selected accounts
    cfg = load_config(_cd())
    if account_id not in cfg.api.selected_accounts:
        cfg.api.selected_accounts.append(account_id)
        save_config(cfg, _cd())

    return meta.model_dump()


@router.post("/accounts/import-from-codex")
async def import_from_codex(req: Request):
    body = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    force = body.get("force", False)
    home = Path.home()
    auth_path = home / ".codex" / "auth.json"
    if not auth_path.exists():
        raise HTTPException(400, "~/.codex/auth.json not found")

    auth_json = auth_path.read_text()
    try:
        auth_data = json.loads(auth_json)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"Invalid auth.json: {e}")

    email = ""
    if "tokens" in auth_data and "id_token" in auth_data["tokens"]:
        import jwt
        try:
            payload = jwt.decode(auth_data["tokens"]["id_token"], options={"verify_signature": False})
            email = payload.get("email", "")
        except Exception:
            pass

    # Check auth mode BEFORE writing files
    if (auth_data.get("auth_mode") or "").lower() != "chatgpt":
        raise HTTPException(400, "UNSUPPORTED_AUTH: Codex Cockpit Lite 只支持 ChatGPT (OAuth) 登录")

    name = email.split("@")[0] if email else "Codex Account"

    chatgpt_account_id = _extract_account_id(auth_data)
    existing = _dedup_account(email, chatgpt_account_id or "")
    if existing and not force:
        raise HTTPException(409, f"DUPLICATE: {existing.id}")
    account_id = existing.id if existing else str(uuid.uuid4())

    ad = account_dir(account_id, _cd())
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "auth.json").write_text(auth_json)

    meta = AccountMeta(
        id=account_id,
        name=name,
        email=email,
        auth_mode=AuthMode("oauth"),
        account_id=chatgpt_account_id or "",
    )
    save_meta(meta, _cd())

    cfg = load_config(_cd())
    if account_id not in cfg.api.selected_accounts:
        cfg.api.selected_accounts.append(account_id)
        save_config(cfg, _cd())

    return meta.model_dump()


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    logger.info(f"DELETE account {account_id}")
    ad = account_dir(account_id, _cd())
    if not ad.exists():
        logger.warning(f"DELETE account {account_id}: not found at {ad}")
        raise HTTPException(404, f"账号 {account_id} 不存在或已被删除")

    shutil.rmtree(ad)
    logger.info(f"DELETE account {account_id}: directory removed")

    cfg = load_config(_cd())
    cfg.api.selected_accounts = [a for a in cfg.api.selected_accounts if a != account_id]
    save_config(cfg, _cd())
    logger.info(f"DELETE account {account_id}: done")

    return {"ok": True}


@router.put("/accounts/{account_id}/toggle")
async def toggle_account(account_id: str, req: Request):
    body = await req.json()
    enabled = body.get("enabled", True)

    cfg = load_config(_cd())
    if enabled:
        if account_id not in cfg.api.selected_accounts:
            cfg.api.selected_accounts.append(account_id)
    else:
        cfg.api.selected_accounts = [a for a in cfg.api.selected_accounts if a != account_id]
    save_config(cfg, _cd())

    return {"ok": True}


@router.post("/accounts/{account_id}/refresh")
async def refresh_account(account_id: str):
    try:
        quota = await py_refresh_quota(account_id, _cd())
        sub = await py_refresh_subscription(account_id, _cd())
        meta = load_meta(account_id, _cd())
        if meta is None:
            raise HTTPException(404, f"账号 {account_id} 不存在")
        return meta.model_dump()
    except Exception as e:
        raise HTTPException(500, str(e))
