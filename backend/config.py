"""Configuration file reader with hot-reload support."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from models import AppConfig, ApiConfig, AccountMeta, AuthMode

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".codex-cockpit"
ENV_CONFIG_DIR = "CODEX_COCKPIT_HOME"


def get_config_dir() -> Path:
    env = os.environ.get(ENV_CONFIG_DIR)
    if env:
        return Path(env).expanduser()
    return DEFAULT_CONFIG_DIR


def ensure_config_dir(config_dir: Optional[Path] = None) -> Path:
    d = config_dir or get_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "accounts").mkdir(exist_ok=True)
    return d


def _default_config_path(config_dir: Optional[Path] = None) -> Path:
    return ensure_config_dir(config_dir) / "config.json"


def load_config(config_dir: Optional[Path] = None) -> AppConfig:
    path = _default_config_path(config_dir)
    if not path.exists():
        cfg = AppConfig()
        save_config(cfg, config_dir)
        return cfg
    try:
        raw = json.loads(path.read_text())
        return AppConfig(**raw)
    except Exception as e:
        logger.warning("Failed to parse config.json, using defaults: %s", e)
        return AppConfig()


def save_config(config: AppConfig, config_dir: Optional[Path] = None) -> None:
    path = _default_config_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config.model_dump(), indent=2, ensure_ascii=False))
    tmp.replace(path)


def get_api_config(config_dir: Optional[Path] = None) -> ApiConfig:
    return load_config(config_dir).api


def accounts_dir(config_dir: Optional[Path] = None) -> Path:
    return ensure_config_dir(config_dir) / "accounts"


def account_dir(account_id: str, config_dir: Optional[Path] = None) -> Path:
    return accounts_dir(config_dir) / account_id


def load_auth_file(account_id: str, config_dir: Optional[Path] = None) -> dict:
    path = account_dir(account_id, config_dir) / "auth.json"
    if not path.exists():
        raise FileNotFoundError(f"auth.json not found for account {account_id}")
    return json.loads(path.read_text())


def load_meta(account_id: str, config_dir: Optional[Path] = None) -> Optional[AccountMeta]:
    path = account_dir(account_id, config_dir) / "meta.json"
    if not path.exists():
        return None
    try:
        return AccountMeta(**json.loads(path.read_text()))
    except Exception as e:
        logger.warning("Failed to parse meta.json for %s: %s", account_id, e)
        return None


def save_meta(meta: AccountMeta, config_dir: Optional[Path] = None) -> None:
    d = account_dir(meta.id, config_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "meta.tmp"
    tmp.write_text(json.dumps(meta.model_dump(), indent=2, ensure_ascii=False))
    tmp.replace(d / "meta.json")


def list_account_metas(config_dir: Optional[Path] = None) -> list[AccountMeta]:
    ad = accounts_dir(config_dir)
    if not ad.exists():
        return []
    metas = []
    for entry in sorted(ad.iterdir()):
        if entry.is_dir():
            meta = load_meta(entry.name, config_dir)
            if meta:
                metas.append(meta)
    return metas


def list_selected_accounts(config_dir: Optional[Path] = None) -> list[AccountMeta]:
    cfg = load_config(config_dir)
    selected_ids = set(cfg.api.selected_accounts)
    all_metas = {m.id: m for m in list_account_metas(config_dir)}
    result = []
    for aid in cfg.api.selected_accounts:
        if aid in all_metas and all_metas[aid].enabled:
            result.append(all_metas[aid])
    return result
