"""Management API — config, accounts, CRUD. Frontend talks to this, not Tauri."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from .auth import extract_email_from_id_token
from .config import (
    account_dir,
    get_config_dir,
    list_account_metas,
    load_config,
    load_meta,
    save_config,
    save_meta,
)
from .models import (
    AccountMeta,
    AppConfig,
    AuthMode,
)
from .quota import refresh_quota as py_refresh_quota
from .quota import refresh_subscription as py_refresh_subscription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
_config_dir: Path | None = None


def set_api_config_dir(path: Path) -> None:
    global _config_dir
    _config_dir = path


def _cd() -> Path:
    return _config_dir or get_config_dir()


def _extract_account_id(auth_data: dict) -> str:
    """Extract account_id from tokens.account_id in auth.json."""
    return (auth_data.get("tokens") or {}).get("account_id", "") or ""


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


async def _read_optional_json_object(req: Request) -> dict:
    raw_body = await req.body()
    if not raw_body:
        return {}
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise HTTPException(400, f"Invalid request JSON: {error}") from error
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid request JSON: expected an object")
    return body


def _normalize_display_name(value: object) -> str:
    if not isinstance(value, str):
        raise HTTPException(400, "显示名称必须是文本")
    display_name = value.strip()
    if len(display_name) > 100:
        raise HTTPException(400, "显示名称不能超过 100 个字符")
    return display_name


# ─── Config ───


@router.get("/config")
async def get_config():
    return load_config(_cd()).model_dump()


@router.get("/config-dir")
def get_config_dir_info():
    return {"path": str(_cd().expanduser())}


@router.put("/config")
async def put_config(body: dict):
    try:
        cfg = AppConfig(**body)
    except ValidationError as error:
        raise HTTPException(400, f"Invalid config: {error}") from error
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
    display_name = body.get("name", "")

    if not auth_json.strip():
        raise HTTPException(400, "auth_json is required")

    # Validate JSON
    try:
        auth_data = json.loads(auth_json)
    except json.JSONDecodeError as error:
        raise HTTPException(400, f"Invalid auth.json: {error}") from error

    # Check auth mode BEFORE writing files
    if auth_data.get("auth_mode") != "chatgpt":
        raise HTTPException(400, "Codex Cockpit Lite 只支持 ChatGPT 登录")

    # Extract email for dedup
    email = ""
    if "tokens" in auth_data and "id_token" in auth_data["tokens"]:
        email = extract_email_from_id_token(auth_data["tokens"]["id_token"])
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
        display_name=_normalize_display_name(display_name),
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
    body = await _read_optional_json_object(req)
    force = body.get("force", False)
    home = Path.home()
    auth_path = home / ".codex" / "auth.json"
    if not auth_path.exists():
        raise HTTPException(400, "~/.codex/auth.json not found")

    auth_json = auth_path.read_text()
    try:
        auth_data = json.loads(auth_json)
    except json.JSONDecodeError as error:
        raise HTTPException(400, f"Invalid auth.json: {error}") from error

    # Check auth mode before decoding identity fields or writing files.
    if auth_data.get("auth_mode") != "chatgpt":
        raise HTTPException(400, "Codex Cockpit Lite 只支持 ChatGPT 登录")

    email = ""
    if "tokens" in auth_data and "id_token" in auth_data["tokens"]:
        email = extract_email_from_id_token(auth_data["tokens"]["id_token"])

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
        display_name=existing.display_name if existing else "",
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


@router.put("/accounts/{account_id}/display-name")
async def update_account_display_name(account_id: str, body: dict):
    meta = load_meta(account_id, _cd())
    if meta is None:
        raise HTTPException(404, f"账号 {account_id} 不存在")
    meta.display_name = _normalize_display_name(body.get("display_name", ""))
    save_meta(meta, _cd())
    return meta.model_dump()


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: str):
    logger.info("DELETE account %s", account_id)
    ad = account_dir(account_id, _cd())
    if not ad.exists():
        logger.warning("DELETE account %s: not found at %s", account_id, ad)
        raise HTTPException(404, f"账号 {account_id} 不存在或已被删除")

    shutil.rmtree(ad)
    logger.info("DELETE account %s: directory removed", account_id)

    cfg = load_config(_cd())
    cfg.api.selected_accounts = [a for a in cfg.api.selected_accounts if a != account_id]
    save_config(cfg, _cd())
    logger.info("DELETE account %s: done", account_id)

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
    if load_meta(account_id, _cd()) is None:
        raise HTTPException(404, f"账号 {account_id} 不存在")
    try:
        await py_refresh_quota(account_id, _cd())
        await py_refresh_subscription(account_id, _cd())
        meta = load_meta(account_id, _cd())
        if meta is None:
            raise HTTPException(404, f"账号 {account_id} 不存在")
        return meta.model_dump()
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("刷新账号 %s 失败", account_id)
        raise HTTPException(500, str(error)) from error
