"""登录 / 权限 / 设置页 / LLM 配置保存的冒烟测试。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DD_INITIAL_PASSWORD", "admin-pass-123")

from web.app import app  # noqa: E402
from dd_agent.runtime_config import (  # noqa: E402
    clear_guest_password,
    get_llm_config,
    set_password,
)


def fresh_admin():
    set_password("admin", "admin-pass-123")
    clear_guest_password()
    client = app.test_client()
    response = client.post("/login", data={"password": "admin-pass-123"}, follow_redirects=False)
    assert response.status_code in (301, 302)
    return client


def test_login_and_settings():
    client = fresh_admin()

    assert client.get("/").status_code == 200
    assert client.get("/settings").status_code == 200

    response = client.post("/api/settings/llm", json={
        "enabled": True,
        "provider": "ollama",
        "ollama": {"base_url": "http://localhost:11434", "model": "qwen2.5:7b"},
        "api": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key": "sk-test"},
    })
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert get_llm_config()["provider"] == "ollama"

    response = client.post("/api/settings/password", json={"old": "admin-pass-123", "new": "admin-new-456"})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_guest_cannot_edit():
    admin = fresh_admin()
    response = admin.post("/api/settings/guest-password", json={"password": "guest-pass-123"})
    assert response.status_code == 200
    assert response.get_json()["guest_enabled"] is True

    guest = app.test_client()
    response = guest.post("/login", data={"password": "guest-pass-123"}, follow_redirects=False)
    assert response.status_code in (301, 302)

    # 访客能看首页
    assert guest.get("/").status_code == 200

    # 访客进不了设置页
    response = guest.get("/settings", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert "/settings" not in response.headers.get("Location", "")

    # 访客改不了模型配置
    response = guest.post("/api/settings/llm", json={"provider": "api", "enabled": False})
    assert response.status_code == 403

    # 访客改不了密码
    response = guest.post("/api/settings/password", json={"old": "guest-pass-123", "new": "hacked123"})
    assert response.status_code == 403


def test_admin_password_change_kills_old_sessions():
    admin = fresh_admin()
    admin.post("/api/settings/guest-password", json={"password": "guest-pass-123"})

    guest = app.test_client()
    guest.post("/login", data={"password": "guest-pass-123"})

    response = admin.post("/api/settings/password", json={"old": "admin-pass-123", "new": "admin-new-456"})
    assert response.status_code == 200

    # 管理员自己这条 session 保住
    assert admin.get("/").status_code == 200

    # 访客的旧登录被踢回登录页
    response = guest.get("/", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert "/login" in response.headers["Location"]


if __name__ == "__main__":
    test_login_and_settings()
    test_guest_cannot_edit()
    test_admin_password_change_kills_old_sessions()
    print("web auth tests passed")
