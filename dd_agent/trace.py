"""
计算底稿：每个关键数字都记下公式、输入、结果和来源文件，方便复核。

输出 calc_trace.json，报告里对应"计算底稿"章节：
    毛利率 = (营业收入 - 营业成本) / 营业收入
    输入：营业收入 1234.3 万，营业成本 800.0 万
    结果：35.2%
    来源：利润表.xlsx
"""
from typing import List


def _wan(value: float) -> str:
    return f"{value / 10000:,.1f} 万"


def _source_files(scanner, categories) -> List[str]:
    names = []
    for f in scanner.files:
        if f.category in categories:
            names.append(f.path.name)
            if len(names) >= 3:
                break
    return names


def _item(name, formula, inputs, result, source, note="") -> dict:
    return {
        "name": name,
        "formula": formula,
        "inputs": inputs,
        "result": result,
        "source": source,
        "note": note,
    }


def build_calc_trace(results: dict, scanner) -> dict:
    items = []
    financial = results.get("financial")
    income = getattr(financial, "monthly_income", None) if financial else None
    balance = getattr(financial, "monthly_balance", None) if financial else None

    income_sources = _source_files(scanner, ["利润表", "财务报表（两表合一）"])
    balance_sources = _source_files(scanner, ["资产负债表", "财务报表（两表合一）"])

    if income is not None and len(income) > 0:
        revenue = float(income["revenue"].sum())
        cogs = float(income["cogs"].sum())
        net_profit = float(income["net_profit"].sum())
        months = len(income)

        items.append(_item(
            "营业收入合计",
            "Σ 每月营业收入",
            {"月份数": months},
            _wan(revenue),
            income_sources,
            "对利润表各月营业收入求和",
        ))

        gross_margin = (revenue - cogs) / revenue if revenue else 0
        items.append(_item(
            "毛利率",
            "(营业收入 - 营业成本) / 营业收入",
            {"营业收入": _wan(revenue), "营业成本": _wan(cogs)},
            f"{gross_margin:.1%}",
            income_sources,
        ))

        items.append(_item(
            "净利润合计",
            "Σ 每月净利润",
            {"月份数": months},
            _wan(net_profit),
            income_sources,
        ))

        avg_revenue = revenue / months if months else 0
        items.append(_item(
            "月均收入",
            "营业收入合计 / 月份数",
            {"营业收入合计": _wan(revenue), "月份数": months},
            _wan(avg_revenue),
            income_sources,
        ))

        if months >= 2:
            std = float(income["revenue"].std())
            cv = std / avg_revenue if avg_revenue else 0
            items.append(_item(
                "收入波动率",
                "月收入标准差 / 月均收入",
                {"月均收入": _wan(avg_revenue), "标准差": _wan(std)},
                f"{cv:.1%}",
                income_sources,
                "越高说明收入越不稳定",
            ))

    if balance is not None and len(balance) > 0:
        last = balance.iloc[-1]
        total_assets = float(last.get("total_assets", 0))
        paid_in = float(last.get("paid_in_capital", 0))
        retained = float(last.get("retained_earnings", 0))
        equity = paid_in + retained
        liabilities = total_assets - equity
        ratio = liabilities / total_assets if total_assets else 0

        if total_assets <= 0:
            ratio_text = "报表异常（总资产≤0），无法计算"
            ratio_note = "总资产为负数，资产负债表严重不平衡，需先核实报表"
        else:
            ratio_text = f"{ratio:.1%}"
            ratio_note = "取资产负债表最后一期"

        items.append(_item(
            "资产负债率",
            "(总资产 - 所有者权益) / 总资产；所有者权益 = 实收资本 + 未分配利润",
            {
                "总资产": _wan(total_assets),
                "实收资本": _wan(paid_in),
                "未分配利润": _wan(retained),
            },
            ratio_text,
            balance_sources,
            ratio_note,
        ))

        cash = float(last.get("cash", 0))
        items.append(_item(
            "期末货币资金",
            "资产负债表最后一期货币资金",
            {"期末": _wan(cash)},
            _wan(cash),
            balance_sources,
        ))

    return {
        "items": items,
        "income_period": f"{income.iloc[0]['month']} ~ {income.iloc[-1]['month']}" if income is not None and len(income) > 0 else "",
        "balance_period": f"{balance.iloc[0]['month']} ~ {balance.iloc[-1]['month']}" if balance is not None and len(balance) > 0 else "",
    }


def to_markdown(trace: dict) -> str:
    items = trace.get("items") or []
    if not items:
        return "（本项目未包含可计算的标准财务报表，暂无计算底稿）"

    lines = []
    lines.append("| 指标 | 公式 | 输入 | 结果 | 来源文件 |")
    lines.append("|---|---|---|---|---|")
    for item in items:
        inputs = "；".join(f"{k}={v}" for k, v in item["inputs"].items())
        source = "、".join(item["source"]) or "—"
        lines.append(
            f"| {item['name']} | `{item['formula']}` | {inputs} | **{item['result']}** | {source} |"
        )
    lines.append("")
    if trace.get("income_period"):
        lines.append(f"- 利润表区间：{trace['income_period']}")
    if trace.get("balance_period"):
        lines.append(f"- 资产负债表区间：{trace['balance_period']}")
    lines.append("- 金额单位：万元；所有指标均由原始报表逐月数据直接计算，未做人工调整。")
    return "\n".join(lines)
