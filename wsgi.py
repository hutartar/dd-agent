"""
gunicorn 入口（Linux / Docker 用）：

    gunicorn -w 2 -b 0.0.0.0:5000 --timeout 300 wsgi:app

本地 Windows 调试直接跑 python -m web.app 或 python web/app.py。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from web.app import app  # noqa: E402

__all__ = ["app"]
