"""
行业包加载器：行业知识全部放 YAML，代码只认接口。

用法：
    from dd_agent.industry import get_industry
    pack = get_industry("hotel")      # 或 "culture-tourism" / "investment" / "generic"
    pack.categories                   # 文件分类词
    pack.redflags                     # 行业红旗
    pack.sections                     # 报告章节
    pack.required_categories          # 缺失资料检查清单
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "industries"


@dataclass
class IndustryPack:
    id: str
    name: str
    description: str = ""
    categories: List[dict] = field(default_factory=list)
    redflags: List[dict] = field(default_factory=list)
    metrics: List[dict] = field(default_factory=list)
    sections: List[dict] = field(default_factory=list)
    required_categories: List[str] = field(default_factory=list)
    benchmarks: Dict[str, dict] = field(default_factory=dict)
    dimensions: List[dict] = field(default_factory=list)  # 分析维度（名称/权重/覆盖的规则类别）
    facts: List[dict] = field(default_factory=list)       # 这个行业要抽哪些事实


def _read(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _merge(parent: dict, child: dict) -> dict:
    """子包覆盖父包：列表追加、字典深合并。"""
    out = dict(parent)
    for key, value in child.items():
        if key in ("categories", "redflags", "sections", "required_categories"):
            out[key] = list(child.get(key) or []) + list(parent.get(key) or [])
        elif key == "dimensions":
            # 维度是整套替换：行业包定义了就用它的，不继承父包
            out[key] = list(child.get(key) or [])
        elif key == "facts":
            # 事实清单追加：行业特有事实 + 通用事实
            out[key] = list(child.get(key) or []) + list(parent.get(key) or [])
        elif key in ("metrics", "benchmarks"):
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = {**out[key], **value}
            else:
                out[key] = list(child.get(key) or []) + list(parent.get(key) or [])
        else:
            out[key] = value
    return out


def _resolve(industry_id: str, seen=None) -> dict:
    seen = seen or set()
    if industry_id in seen:
        return {}
    seen.add(industry_id)

    path = CONFIG_DIR / f"{industry_id}.yaml"
    if not path.exists():
        return {}

    data = _read(path)
    parent_id = data.get("extends")
    if parent_id:
        parent = _resolve(parent_id, seen)
        data = _merge(parent, data)
    return data


def get_industry(industry_id: str = None) -> IndustryPack:
    """加载行业包；不存在就回退到 generic。"""
    industry_id = (industry_id or "generic").strip()
    data = _resolve(industry_id)
    if not data:
        data = _resolve("generic")
    # 去重（同一分类/规则可能被父子包重复声明）
    categories = []
    seen_categories = set()
    for item in data.get("categories") or []:
        key = item.get("category")
        if key and key not in seen_categories:
            seen_categories.add(key)
            categories.append(item)

    redflags = []
    seen_ids = set()
    for item in data.get("redflags") or []:
        key = item.get("id")
        if key and key not in seen_ids:
            seen_ids.add(key)
            redflags.append(item)

    return IndustryPack(
        id=data.get("id") or industry_id,
        name=data.get("name") or industry_id,
        description=data.get("description") or "",
        categories=categories,
        redflags=redflags,
        metrics=list(data.get("metrics") or []),
        sections=list(data.get("sections") or []),
        required_categories=list(data.get("required_categories") or []),
        benchmarks=dict(data.get("benchmarks") or {}),
        dimensions=list(data.get("dimensions") or []),
        facts=list(data.get("facts") or []),
    )


def list_industries() -> List[dict]:
    """给前端下拉用。"""
    items = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        data = _read(path)
        items.append({
            "id": data.get("id") or path.stem,
            "name": data.get("name") or path.stem,
            "description": data.get("description") or "",
        })
    # generic 放最前
    items.sort(key=lambda item: (item["id"] != "generic", item["id"]))
    return items
