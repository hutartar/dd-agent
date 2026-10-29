"""
访客个人模型配置：按访客隔离，api_key 用 Fernet 加密存盘。

- data/visitors/<visitor_id>.json   配置（api_key 只存密文）
- data/visitor_secret.key           加密密钥（首次自动生成）

访客换浏览器会生成新的 visitor_id，旧配置不会串到别人身上。
"""
import json
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .runtime_config import DATA_DIR

VISITOR_DIR = DATA_DIR / "visitors"
SECRET_PATH = DATA_DIR / "visitor_secret.key"


def _fernet() -> Fernet:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_bytes(Fernet.generate_key())
    return Fernet(SECRET_PATH.read_bytes())


def new_visitor_id() -> str:
    return uuid.uuid4().hex


def _path(visitor_id: str) -> Path:
    return VISITOR_DIR / f"{visitor_id}.json"


def load_visitor_config(visitor_id: str) -> dict:
    if not visitor_id:
        return {}
    try:
        data = json.loads(_path(visitor_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    api = dict(data.get("api") or {})
    encrypted = api.pop("api_key_enc", "")
    if encrypted:
        try:
            api["api_key"] = _fernet().decrypt(encrypted.encode()).decode()
        except (InvalidToken, ValueError):
            api["api_key"] = ""
    data["api"] = api
    return data


def save_visitor_config(visitor_id: str, config: dict) -> dict:
    if not visitor_id:
        visitor_id = new_visitor_id()

    api = dict(config.get("api") or {})
    key = (api.pop("api_key", "") or "").strip()

    # 没传新 key（或传的是掩码）就保留旧密文
    old_encrypted = ""
    try:
        old = json.loads(_path(visitor_id).read_text(encoding="utf-8"))
        old_encrypted = (old.get("api") or {}).get("api_key_enc", "")
    except (OSError, ValueError):
        old_encrypted = ""

    if key and not key.startswith("••••"):
        api["api_key_enc"] = _fernet().encrypt(key.encode()).decode()
    elif old_encrypted:
        api["api_key_enc"] = old_encrypted

    provider = (config.get("provider") or "ollama").strip().lower()
    if provider not in ("ollama", "api"):
        provider = "ollama"

    data = {
        "enabled": bool(config.get("enabled", True)),
        "provider": provider,
        "ollama": {
            "base_url": ((config.get("ollama") or {}).get("base_url") or "").strip(),
            "model": ((config.get("ollama") or {}).get("model") or "").strip(),
        },
        "api": {
            "base_url": ((config.get("api") or {}).get("base_url") or "").strip(),
            "model": ((config.get("api") or {}).get("model") or "").strip(),
        },
    }
    if api.get("api_key_enc"):
        data["api"]["api_key_enc"] = api["api_key_enc"]

    VISITOR_DIR.mkdir(parents=True, exist_ok=True)
    _path(visitor_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_visitor_config(visitor_id)


def clear_visitor_config(visitor_id: str) -> None:
    if not visitor_id:
        return
    try:
        _path(visitor_id).unlink()
    except OSError:
        pass
