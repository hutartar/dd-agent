"""
监控：同一项目重跑对比（diff）+ 多项目组合视图（portfolio）。

用法：
    python -m dd_agent portfolio web/results
    python -m dd_agent diff web/results/task_001 web/results/task_002
"""
import json
from pathlib import Path


def _read_json(path: Path, default=None):
    try:
        # utf-8-sig：兼容 Windows 记事本/PowerShell 写出的 BOM
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_task(task_dir) -> dict:
    task_dir = Path(task_dir)
    output = task_dir / "output"
    return {
        "id": task_dir.name,
        "meta": _read_json(output / "meta.json", {}) or {},
        "verdict": _read_json(output / "verdict.json"),
        "inventory": _read_json(output / "file_inventory.json", {}) or {},
    }


def _risk_names(verdict: dict) -> set:
    return {item.get("name") for item in (verdict.get("top_risks") or []) if item.get("name")}


def compare(old_task, new_task) -> dict:
    old = load_task(old_task)
    new = load_task(new_task)
    old_verdict = old.get("verdict") or {}
    new_verdict = new.get("verdict") or {}

    return {
        "old": {"id": old["id"], "grade": old_verdict.get("grade"), "score": old_verdict.get("score")},
        "new": {"id": new["id"], "grade": new_verdict.get("grade"), "score": new_verdict.get("score")},
        "score_delta": round((new_verdict.get("score") or 0) - (old_verdict.get("score") or 0), 1),
        "risk_added": sorted(_risk_names(new_verdict) - _risk_names(old_verdict)),
        "risk_removed": sorted(_risk_names(old_verdict) - _risk_names(new_verdict)),
        "files": {
            "old": old["inventory"].get("total_files"),
            "new": new["inventory"].get("total_files"),
        },
    }


def portfolio(results_root) -> list:
    results_root = Path(results_root)
    rows = []
    for task_dir in sorted(results_root.iterdir()):
        if not task_dir.is_dir() or task_dir.name.startswith("."):
            continue
        task = load_task(task_dir)
        verdict = task.get("verdict") or {}
        meta = task.get("meta") or {}
        rows.append({
            "id": task["id"],
            "company": meta.get("company_name") or meta.get("project_name") or task["id"],
            "grade": verdict.get("grade"),
            "score": verdict.get("score"),
            "industry": (verdict.get("industry") or {}).get("name"),
            "risk_count": len(verdict.get("top_risks") or []),
        })
    return rows
