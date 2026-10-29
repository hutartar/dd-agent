"""
老版 .doc 转换：优先 LibreOffice，其次 Word COM（需要 pywin32 + Office）。

都没有时返回 None，由调用方在报告里提示"装 LibreOffice 或手动另存为 .docx"。
转换结果按文件 MD5 缓存到 data/converted/，同一个文件只转一次。
"""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from ..runtime_config import DATA_DIR

CACHE_DIR = DATA_DIR / "converted"


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_soffice():
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _convert_with_soffice(soffice: str, src: Path, out_dir: Path):
    subprocess.run(
        [soffice, "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(src)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    target = out_dir / (src.stem + ".docx")
    return target if target.exists() else None


def _convert_with_word(src: Path, out_dir: Path):
    try:
        import win32com.client
    except ImportError:
        return None

    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(src))
        target = out_dir / (src.stem + ".docx")
        doc.SaveAs2(str(target), FileFormat=16)  # wdFormatXMLDocument
        doc.Close(False)
        return target if target.exists() else None
    except Exception:
        return None
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass


def convert_doc(src) -> Path | None:
    """把 .doc 转成 .docx，返回缓存路径；转不了返回 None。"""
    src = Path(src)
    if src.suffix.lower() != ".doc":
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / (_md5(src) + ".docx")
    if target.exists():
        return target

    tmp_dir = CACHE_DIR / "tmp"
    tmp_dir.mkdir(exist_ok=True)

    result = None
    soffice = find_soffice()
    if soffice:
        try:
            result = _convert_with_soffice(soffice, src, tmp_dir)
        except Exception:
            result = None
    if result is None:
        result = _convert_with_word(src, tmp_dir)

    if result is None or not result.exists():
        return None

    os.replace(result, target)
    return target


def available() -> bool:
    """当前机器有没有可用的转换工具。"""
    if find_soffice():
        return True
    try:
        import win32com.client  # noqa: F401
        return True
    except ImportError:
        return False
