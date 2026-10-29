"""
风控结论层：把模块分析收敛成一个可解释的结论。

输入：文件盘点 + 红旗命中 + 行业包
输出：风险等级、分项得分、核心理由、Top 风险、缺失资料、建议动作、覆盖度
"""
from typing import Dict, List

DIMENSIONS = [
    ("财务真实性", 0.35),
    ("业务可持续性", 0.20),
    ("法律合规", 0.25),
    ("资质有效性", 0.10),
    ("管理层与股东", 0.10),
]

# 规则库里的细分 category 归一到五个评分维度
CATEGORY_TO_DIMENSION = {
    "财务真实性": "财务真实性",
    "收入真实性": "财务真实性",
    "成本与费用质量": "财务真实性",
    "成本与毛利质量": "财务真实性",
    "盈利可持续性": "财务真实性",
    "偿债能力": "财务真实性",
    "负债完整性": "财务真实性",
    "资产质量": "财务真实性",
    "运营效率": "财务真实性",
    "业务可持续性": "业务可持续性",
    "业务稳定性": "业务可持续性",
    "法律合规": "法律合规",
    "资质有效性": "资质有效性",
    "管理层与股东": "管理层与股东",
}


def _dimension_of(category: str) -> str:
    return CATEGORY_TO_DIMENSION.get(category, category)

# 每条红旗对应去哪几类文件里找证据
EVIDENCE_CATEGORIES = {
    "财务真实性": [
        "利润表", "资产负债表", "财务报表（两表合一）",
        "银行流水", "审计报告", "纳税申报", "收入流水", "成本费用明细",
    ],
    "业务可持续性": ["PMS运营数据", "OTA渠道协议", "景区运营数据", "商业计划书"],
    "法律合规": ["租赁合同", "重大合同", "投资协议", "未决诉讼", "行政处罚", "对外担保"],
    "资质有效性": ["营业执照", "特种行业许可", "消防验收", "卫生许可", "旅游相关资质"],
    "管理层与股东": ["股东名册", "股权架构", "公司章程", "工商变更", "征信报告"],
}

SEVERITY_PENALTY = {"high": 12.0, "medium": 6.0, "low": 2.0}


# 细分规则类别优先匹配更精确的报表，避免所有财务风险都指向同一张表
CATEGORY_EVIDENCE = {
    "资产质量": ["资产负债表", "财务报表（两表合一）"],
    "负债完整性": ["资产负债表"],
    "偿债能力": ["资产负债表", "银行流水"],
    "盈利可持续性": ["利润表", "财务报表（两表合一）"],
    "收入真实性": ["利润表", "纳税申报", "银行流水"],
    "成本与费用质量": ["利润表", "成本费用明细"],
    "成本与毛利质量": ["利润表"],
    "运营效率": ["利润表", "资产负债表"],
    "业务稳定性": ["PMS运营数据", "景区运营数据", "商业计划书"],
}


BALANCE_WORDS = ["资产总计", "负债", "应付", "应收", "货币资金", "未分配利润", "实收资本", "所有者权益"]
INCOME_WORDS = ["营业收入", "营业成本", "毛利率", "净利润", "费用", "收入"]


def _statement_by_name(name: str):
    if any(word in name for word in BALANCE_WORDS):
        return ["资产负债表", "财务报表（两表合一）"]
    if any(word in name for word in INCOME_WORDS):
        return ["利润表", "财务报表（两表合一）"]
    return None


def _evidence_for(scanner, category: str, dimension: str, name: str = "") -> List[dict]:
    """给一条风险找可能的证据文件，最多 3 个；优先按规则名判断该看哪张表。"""
    wanted = (
        _statement_by_name(name)
        or CATEGORY_EVIDENCE.get(category)
        or EVIDENCE_CATEGORIES.get(dimension, [])
    )
    items = []
    for category_name in wanted:
        for f in scanner.files:
            if f.category == category_name:
                item_name = f.path.name
                if len(item_name) > 50:
                    item_name = f"{f.path.stem[:38]}…{f.path.suffix}"
                items.append({"name": item_name, "source": str(f.path)})
                if len(items) >= 3:
                    return items
    return items


def _grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def build_verdict(scanner, redflags, industry, results: dict = None,
                  fact_summary: dict = None) -> Dict:
    results = results or {}
    total = getattr(scanner, "total_files", 0) or 0
    unclassified = sum(1 for f in scanner.files if f.module == "other")
    unclassified_rate = unclassified / total if total else 0.0

    # ---- 维度：由行业包定义，没定义就回退通用五维 ----
    pack_dims = getattr(industry, "dimensions", None) or []
    if pack_dims:
        pairs = [
            (d.get("name"), float(d.get("weight", 0) or 0))
            for d in pack_dims
            if d.get("name")
        ]
        total_weight = sum(w for _, w in pairs) or 1.0
        dims = [(name, w / total_weight) for name, w in pairs]
        category_map = {}
        for d in pack_dims:
            for c in (d.get("categories") or []):
                category_map[c] = d.get("name")
    else:
        dims = DIMENSIONS
        category_map = CATEGORY_TO_DIMENSION

    def dim_of(category: str) -> str:
        return category_map.get(category, category)

    # ---- 分项得分 ----
    dimensions = []
    for name, weight in dims:
        score = 100.0
        for hit in redflags:
            if dim_of(getattr(hit, "category", "")) == name:
                score -= SEVERITY_PENALTY.get(hit.severity, 3.0)
        score = max(0.0, min(100.0, score))
        dimensions.append({"name": name, "score": round(score, 1), "weight": round(weight, 3)})

    weighted = sum(item["score"] * item["weight"] for item in dimensions)

    # ---- 资料完整度 ----
    categories = {f.category for f in scanner.files}
    missing = [c for c in industry.required_categories if c not in categories]
    completeness_penalty = min(len(missing) * 4, 20)

    # ---- 未分类惩罚 ----
    if unclassified_rate > 0.30:
        unclassified_penalty = 12
    elif unclassified_rate > 0.15:
        unclassified_penalty = 6
    elif unclassified_rate > 0.05:
        unclassified_penalty = 2
    else:
        unclassified_penalty = 0

    # ---- 模块降级惩罚：跑失败但没让报告崩的步骤，要如实扣分 ----
    steps = results.get("steps_status") or []
    degraded_modules = [
        step.get("name", "") for step in steps
        if step.get("status") == "error" and step.get("level") == "degraded"
    ]
    warning_modules = [
        step.get("name", "") for step in steps
        if step.get("status") == "error" and step.get("level") == "warning"
    ]
    quality_penalty = min(len(degraded_modules) * 3 + len(warning_modules), 12)

    score = max(0.0, min(100.0, weighted - completeness_penalty - unclassified_penalty - quality_penalty))

    # 高风险一票否决：数量越多，总分上限越低，避免被 100 分的维度平均掉
    high_count = len([h for h in redflags if h.severity == "high"])
    if high_count >= 5:
        score = min(score, 50.0)
    elif high_count >= 3:
        score = min(score, 65.0)
    elif high_count >= 1:
        score = min(score, 80.0)

    # 资料不全不准给高分：置信度低就压上限，避免"没查到问题"被当成"没问题"
    confidence = (fact_summary or {}).get("confidence", 1.0)
    if confidence < 0.30:
        score = min(score, 55.0)
    elif confidence < 0.50:
        score = min(score, 70.0)
    elif confidence < 0.70:
        score = min(score, 82.0)

    score = round(score, 1)

    # ---- 核心理由 ----
    high_hits = [h for h in redflags if h.severity == "high"]
    reasons = []
    for hit in high_hits[:3]:
        reasons.append(f"{hit.name}：{hit.explanation}")
    if missing:
        reasons.append("关键资料缺失：" + "、".join(missing[:4]))
    if unclassified_rate > 0.05:
        reasons.append(f"未分类文件占 {unclassified_rate:.0%}，覆盖度不足")
    if degraded_modules:
        reasons.append("模块降级：" + "、".join(degraded_modules) + "（相关结论缺失）")
    if not reasons:
        reasons.append("未命中高风险规则，主要指标在可接受区间")

    # ---- 全部风险（带证据，报告里逐条可点） ----
    all_risks = []
    for hit in redflags:
        dimension = dim_of(getattr(hit, "category", ""))
        all_risks.append({
            "name": hit.name,
            "severity": hit.severity,
            "category": getattr(hit, "category", ""),
            "explanation": hit.explanation,
            "evidence": _evidence_for(scanner, getattr(hit, "category", ""), dimension, hit.name),
        })

    top_risks = all_risks[:5]

    # ---- 建议动作 ----
    actions = []
    if missing:
        actions.append("补齐资料：" + "、".join(missing[:5]))
    if high_hits:
        actions.append("优先核实：" + "、".join(h.name for h in high_hits[:3]))
    if unclassified_rate > 0.05:
        actions.append("在报告页把未分类文件指定分类，或补充可识别的命名/目录")
    actions.append("投后监控：按季度重跑本项目，对比收入、现金流和红旗变化")

    # ---- 覆盖度 ----
    coverage = {
        "total_files": total,
        "classified": total - unclassified,
        "unclassified": unclassified,
        "unclassified_rate": round(unclassified_rate, 3),
        "p0_files": sum(1 for f in scanner.files if f.priority == "P0"),
    }

    return {
        "grade": _grade(score),
        "score": score,
        "industry": {"id": industry.id, "name": industry.name},
        "dimensions": dimensions,
        "reasons": reasons,
        "top_risks": top_risks,
        "all_risks": all_risks,
        "degraded_modules": degraded_modules,
        "warning_modules": warning_modules,
        "missing": missing,
        "actions": actions,
        "coverage": coverage,
        "confidence": (fact_summary or {}).get("confidence", 0.0),
        "fact_coverage": (fact_summary or {}).get("coverage", {}),
        "consistency": (fact_summary or {}).get("consistency", {}),
    }


def to_markdown(verdict: Dict) -> str:
    """一页纸结论，直接嵌进报告。"""
    lines = []
    grade = verdict["grade"]
    lines.append(f"**风险等级：{grade}（{verdict['score']} 分）**")
    confidence = verdict.get("confidence")
    if confidence:
        coverage = (verdict.get("fact_coverage") or {}).get("rate", 0)
        lines.append(
            f"*数据置信度：{confidence:.0%}（关键事实覆盖 {coverage:.0%}）*"
        )
    lines.append("")
    lines.append("| 维度 | 得分 | 权重 |")
    lines.append("|---|---|---|")
    for item in verdict["dimensions"]:
        lines.append(f"| {item['name']} | {item['score']} | {item['weight']:.0%} |")
    lines.append("")
    lines.append("**核心理由**")
    for reason in verdict["reasons"]:
        lines.append(f"- {reason}")
    lines.append("")
    if verdict["top_risks"]:
        lines.append("**Top 风险**")
        for risk in verdict["top_risks"]:
            parts = []
            for item in risk.get("evidence") or []:
                if isinstance(item, dict):
                    name = item.get("name") or ""
                    path = item.get("path") or ""
                    parts.append(f"[{name}]({path})" if path else name)
                else:
                    parts.append(str(item))
            evidence = f"（证据：{'、'.join(parts)}）" if parts else ""
            lines.append(f"- [{risk['severity']}] {risk['name']}：{risk['explanation']}{evidence}")
        lines.append("")
    if verdict["missing"]:
        lines.append("**缺失资料**")
        lines.append("- " + "、".join(verdict["missing"]))
        lines.append("")
    if verdict.get("degraded_modules"):
        lines.append("**模块降级**")
        lines.append("- " + "、".join(verdict["degraded_modules"]) + " 未成功执行，相关结论缺失")
        lines.append("")
    lines.append("**建议动作**")
    for action in verdict["actions"]:
        lines.append(f"- {action}")
    lines.append("")
    coverage = verdict["coverage"]
    lines.append(
        f"*覆盖度：{coverage['classified']}/{coverage['total_files']} 个文件已分类，"
        f"未分类率 {coverage['unclassified_rate']:.1%}，P0 文件 {coverage['p0_files']} 个。*"
    )
    return "\n".join(lines)
