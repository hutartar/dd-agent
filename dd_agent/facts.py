"""
事实层：把文件里的关键数字抽成带来源的事实，供红旗、交叉验证、评分共用。

最小版先接财务和合同两类事实：
- 财务：营业收入、毛利率、净利润、总资产
- 合同：年租金、租期、押金、诉讼金额、罚款金额

每个事实带来源文件和置信度；同一事实多个来源时自动比对一致性。
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Fact:
    id: str
    name: str
    value: Any
    unit: str = ""
    source: str = ""
    confidence: float = 1.0
    note: str = ""


@dataclass
class FactSet:
    facts: List[Fact] = field(default_factory=list)

    def add(self, fact: Fact) -> None:
        self.facts.append(fact)

    def get(self, fact_id: str) -> List[Fact]:
        return [f for f in self.facts if f.id == fact_id]

    def first(self, fact_id: str) -> Optional[Fact]:
        items = self.get(fact_id)
        return items[0] if items else None

    def ids(self) -> set:
        return {f.id for f in self.facts}

    def to_dict(self) -> dict:
        return {"facts": [asdict(f) for f in self.facts]}


def _first_file(scanner, categories) -> str:
    if not scanner:
        return ""
    for f in scanner.files:
        if f.category in categories:
            return f.path.name
    return ""


def _check_consistency(facts: List[Fact], tolerance: float = 0.05) -> dict:
    """同一事实的多来源比对：差 5% 以内算一致。"""
    values = [f.value for f in facts if isinstance(f.value, (int, float))]
    if len(values) < 2:
        return {"status": "single_source", "sources": len(facts)}
    lo, hi = min(values), max(values)
    base = abs(hi) if hi else 1
    diff = (hi - lo) / base
    return {
        "status": "consistent" if diff <= tolerance else "inconsistent",
        "sources": len(facts),
        "diff": round(diff, 3),
    }


def extract_facts(financial=None, legal=None, scanner=None) -> FactSet:
    """从财务、法律模块和文件清单里抽事实。"""
    fs = FactSet()

    if financial is not None:
        income = getattr(financial, "monthly_income", None)
        balance = getattr(financial, "monthly_balance", None)
        income_source = _first_file(scanner, ["利润表", "财务报表（两表合一）"])
        balance_source = _first_file(scanner, ["资产负债表", "财务报表（两表合一）"])

        if income is not None and len(income) > 0:
            revenue = float(income["revenue"].sum())
            cogs = float(income["cogs"].sum())
            profit = float(income["net_profit"].sum())
            months = len(income)
            fs.add(Fact("revenue", "营业收入合计", round(revenue, 2), "元",
                        income_source, 0.9, f"{months}个月合计"))
            if revenue:
                fs.add(Fact("gross_margin", "毛利率", round((revenue - cogs) / revenue, 4),
                            "", income_source, 0.9))
            fs.add(Fact("net_profit", "净利润合计", round(profit, 2), "元",
                        income_source, 0.9, f"{months}个月合计"))

        if balance is not None and len(balance) > 0:
            last = balance.iloc[-1]
            fs.add(Fact("total_assets", "总资产", round(float(last.get("total_assets", 0)), 2),
                        "元", balance_source, 0.9))
            fs.add(Fact("retained_earnings", "未分配利润",
                        round(float(last.get("retained_earnings", 0)), 2),
                        "元", balance_source, 0.9))

    if legal is not None:
        for lease in (getattr(legal, "leases", None) or [])[:3]:
            if lease.total_rent:
                fs.add(Fact("lease_rent", "年租金", float(lease.total_rent), "元",
                            lease.file_name, 0.8))
            if lease.lease_term:
                fs.add(Fact("lease_term", "租期", lease.lease_term, "", lease.file_name, 0.8))
            if lease.deposit:
                fs.add(Fact("lease_deposit", "押金", float(lease.deposit), "元",
                            lease.file_name, 0.8))

        for lawsuit in (getattr(legal, "lawsuits", None) or [])[:5]:
            if lawsuit.amount:
                fs.add(Fact("litigation_amount", "诉讼金额", float(lawsuit.amount), "元",
                            lawsuit.file_name, 0.8))

        for penalty in (getattr(legal, "penalties", None) or [])[:5]:
            if penalty.amount:
                fs.add(Fact("penalty_amount", "罚款金额", float(penalty.amount), "元",
                            penalty.file_name, 0.8))

    return fs


def _required_ids(required) -> List[str]:
    ids = []
    for item in required or []:
        if isinstance(item, dict):
            if item.get("id"):
                ids.append(item["id"])
        elif item:
            ids.append(str(item))
    return ids


def _source_matrix(factset: FactSet, required) -> list:
    """每个事实应该有哪些来源、实际有哪些来源。"""
    rows = []
    for item in required or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        fid = item["id"]
        expected = item.get("sources") or []
        actual = []
        for fact in factset.get(fid):
            if fact.source and fact.source not in actual:
                actual.append(fact.source)
        rows.append({
            "id": fid,
            "name": item.get("name") or fid,
            "expected": expected,
            "present": actual,
            "coverage": round(len(actual) / len(expected), 2) if expected else 0.0,
        })
    return rows


def compute_coverage(factset: FactSet, required) -> dict:
    ids = _required_ids(required)
    have = factset.ids()
    present = [r for r in ids if r in have]
    return {
        "required": ids,
        "present": present,
        "missing": [r for r in ids if r not in have],
        "rate": round(len(present) / len(ids), 2) if ids else 0.0,
    }


def summarize(factset: FactSet, required=None) -> dict:
    """覆盖度 + 来源矩阵 + 一致性 + 置信度。"""
    ids = factset.ids()

    consistency = {}
    for fid in ids:
        items = factset.get(fid)
        if len(items) > 1:
            consistency[fid] = _check_consistency(items)

    coverage = compute_coverage(factset, required)
    source_matrix = _source_matrix(factset, required)
    crosscheck_rate = 0.0
    if consistency:
        ok = sum(1 for v in consistency.values() if v["status"] == "consistent")
        crosscheck_rate = round(ok / len(consistency), 2)

    # 置信度：基础 0.3 + 覆盖度 0.5 + 交叉验证 0.2
    confidence = 0.3 + 0.5 * coverage["rate"] + 0.2 * crosscheck_rate

    return {
        "coverage": coverage,
        "source_matrix": source_matrix,
        "consistency": consistency,
        "crosscheck_rate": crosscheck_rate,
        "confidence": round(min(confidence, 1.0), 2),
        "fact_count": len(factset.facts),
        "unique_facts": len(ids),
    }


def to_markdown(factset: FactSet, summary: dict) -> str:
    """事实与来源清单。"""
    lines = []
    coverage = summary.get("coverage") or {}
    lines.append(
        f"**数据置信度：{summary.get('confidence', 0):.0%}**"
        f"（关键事实覆盖 {coverage.get('rate', 0):.0%}，多源一致率 {summary.get('crosscheck_rate', 0):.0%}）"
    )
    lines.append("")
    if not factset.facts:
        return "\n".join(lines + ["（未抽取到结构化事实）"])

    lines.append("| 事实 | 值 | 来源 | 置信度 |")
    lines.append("|---|---|---|---|")
    for fact in factset.facts[:20]:
        value = fact.value
        if isinstance(value, float):
            value = f"{value:,.2f}"
        note = f"（{fact.note}）" if fact.note else ""
        lines.append(f"| {fact.name} | {value}{fact.unit} | {fact.source or '—'} | {fact.confidence:.0%} |")
    lines.append("")

    rows = summary.get("source_matrix") or []
    if rows:
        lines.append("**来源覆盖**")
        lines.append("")
        lines.append("| 事实 | 预期来源 | 实际来源 | 覆盖 |")
        lines.append("|---|---|---|---|")
        for row in rows:
            expected = "、".join(row["expected"]) or "—"
            present = "、".join(row["present"]) or "—"
            lines.append(f"| {row['name']} | {expected} | {present} | {row['coverage']:.0%} |")
        lines.append("")

    inconsistent = [
        fid for fid, item in (summary.get("consistency") or {}).items()
        if item.get("status") == "inconsistent"
    ]
    if inconsistent:
        lines.append(f"⚠️ 多来源不一致：{'、'.join(inconsistent)}")
        lines.append("")
    missing = coverage.get("missing") or []
    if missing:
        lines.append(f"缺失事实：{'、'.join(missing)}")
        lines.append("")

    return "\n".join(lines)
