"""
运行时可改配置：网页上改，立即生效，不用重启、不用动代码。

settings.json   LLM 配置（Ollama / OpenAI 兼容 API 两套独立保存，切换不丢）
auth.json       登录密码哈希
secret_key      Flask session 密钥（持久化，重启后不用重新登录）

优先级：显式参数 > settings.json > 环境变量 > 代码默认值。
"""
import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DD_DATA_DIR") or (ROOT / "data"))

SETTINGS_PATH = DATA_DIR / "settings.json"
AUTH_PATH = DATA_DIR / "auth.json"
SECRET_PATH = DATA_DIR / "secret_key"

DEFAULT_LLM = {
    "enabled": True,
    "provider": "ollama",  # ollama | api
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "qwen2.5:7b",
    },
    "api": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key": "",
    },
}


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default):
    try:
        # utf-8-sig：兼容记事本/PowerShell 写出的 BOM
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data):
    _ensure_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- LLM 设置

def load_settings() -> dict:
    """给设置页用的完整配置（带默认值，两套引擎分开保留）。"""
    raw = _read_json(SETTINGS_PATH, {})
    llm = raw.get("llm") or {}
    merged = {
        "enabled": bool(llm.get("enabled", DEFAULT_LLM["enabled"])),
        "provider": llm.get("provider", DEFAULT_LLM["provider"]),
        "ollama": {**DEFAULT_LLM["ollama"], **(llm.get("ollama") or {})},
        "api": {**DEFAULT_LLM["api"], **(llm.get("api") or {})},
    }
    return {"llm": merged}


def save_settings(patch: dict) -> dict:
    current = load_settings()["llm"]
    incoming = patch.get("llm") or {}
    merged = {**current}

    if "enabled" in incoming:
        merged["enabled"] = bool(incoming["enabled"])
    if incoming.get("provider") in ("ollama", "api"):
        merged["provider"] = incoming["provider"]

    for section in ("ollama", "api"):
        if isinstance(incoming.get(section), dict):
            merged[section] = {**current[section], **incoming[section]}

    _write_json(SETTINGS_PATH, {"llm": merged})
    return {"llm": merged}


def get_llm_config() -> dict:
    """
    给运行时用的扁平配置。文件不存在就返回 {}，让调用方回退到环境变量。

    DD_IGNORE_SETTINGS=1：跳过服务器全局设置，改用环境变量里传进来的配置。
    用于"使用者自带 API"的临时任务，key 只在该子进程里存在，不落盘。
    """
    if os.getenv("DD_IGNORE_SETTINGS") == "1":
        return {}
    raw = _read_json(SETTINGS_PATH, None)
    if not raw:
        return {}
    llm = raw.get("llm") or {}
    if not llm:
        return {}
    provider = (llm.get("provider") or "ollama").strip().lower()
    section = llm.get(provider) or {}
    return {
        "enabled": bool(llm.get("enabled", True)),
        "provider": provider,
        "base_url": (section.get("base_url") or "").strip(),
        "model": (section.get("model") or "").strip(),
        "api_key": section.get("api_key") or "",
    }


# ---------------------------------------------------------------- 登录密码

def _load_auth() -> dict:
    return _read_json(AUTH_PATH, {}) or {}


def _hash_of(role: str) -> str:
    data = _load_auth()
    if role == "admin":
        # 兼容旧格式：只有 password_hash 时视为管理员
        return data.get("admin_hash") or data.get("password_hash") or ""
    return data.get("guest_hash") or ""


def auth_version() -> int:
    return int(_load_auth().get("version", 0) or 0)


def has_guest_password() -> bool:
    return bool(_hash_of("guest"))


def verify_password(password: str):
    """密码对得上管理员返回 admin，对得上访客返回 guest，都不对返回 None。"""
    from werkzeug.security import check_password_hash
    admin = _hash_of("admin")
    if admin and check_password_hash(admin, password):
        return "admin"
    guest = _hash_of("guest")
    if guest and check_password_hash(guest, password):
        return "guest"
    return None


def set_password(role: str, password: str) -> int:
    """改密码并把版本 +1：旧 session 全部失效，需要重新登录。"""
    from werkzeug.security import generate_password_hash
    data = _load_auth()
    key = "admin_hash" if role == "admin" else "guest_hash"
    data[key] = generate_password_hash(password)
    data.pop("password_hash", None)
    data["version"] = auth_version() + 1
    _write_json(AUTH_PATH, data)
    return data["version"]


def clear_guest_password() -> int:
    data = _load_auth()
    data.pop("guest_hash", None)
    data["version"] = auth_version() + 1
    _write_json(AUTH_PATH, data)
    return data["version"]


def ensure_initial_password():
    """
    第一次启动时初始化密码：
    - 管理员：DD_INITIAL_PASSWORD，没有就随机生成（返回给启动日志打印）
    - 访客：DD_GUEST_PASSWORD，可选；不设就不开放访客登录
    """
    if _hash_of("admin"):
        return None

    initial = os.getenv("DD_INITIAL_PASSWORD", "").strip()
    generated = None
    if initial:
        set_password("admin", initial)
    else:
        generated = secrets.token_urlsafe(9)
        set_password("admin", generated)

    guest = os.getenv("DD_GUEST_PASSWORD", "").strip()
    if guest:
        set_password("guest", guest)

    return generated


# ---------------------------------------------------------------- session 密钥

def load_secret_key() -> str:
    _ensure_dir()
    if SECRET_PATH.exists():
        try:
            key = SECRET_PATH.read_text(encoding="utf-8").strip()
            if key:
                return key
        except OSError:
            pass
    key = secrets.token_hex(32)
    SECRET_PATH.write_text(key, encoding="utf-8")
    return key
