"""
图表生成模块：5张标配尽调图表
配色: 深蓝#165DFF + 红色#F53F3F + 灰色#86909C
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from pathlib import Path
import pandas as pd

# 字体配置（Windows 用微软雅黑，Linux/Docker 用 Noto CJK）
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["font.size"] = 10

BLUE = "#165DFF"
RED = "#F53F3F"
GRAY = "#86909C"
LIGHT_BLUE = "#94BFFF"


def _ensure_dir(path: str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def chart1_revenue_profit(income_df: pd.DataFrame, output_dir: str) -> str:
    """图1：月度收入+净利润双轴图"""
    fig, ax1 = plt.subplots(figsize=(10, 5.5), dpi=150)

    months = income_df["month"].tolist()
    revenue = income_df["revenue"].tolist()
    net_profit = income_df["net_profit"].tolist()

    x = range(len(months))
    bars = ax1.bar(x, revenue, color=BLUE, alpha=0.8, label="月收入（万元）")
    ax1.set_ylabel("收入（万元）", color=BLUE)
    ax1.tick_params(axis="y", labelcolor=BLUE)

    ax2 = ax1.twinx()
    line = ax2.plot(x, net_profit, color=RED, marker="o", linewidth=2, label="净利润（万元）")
    ax2.axhline(y=0, color=GRAY, linestyle="--", linewidth=0.8)
    ax2.set_ylabel("净利润（万元）", color=RED)
    ax2.tick_params(axis="y", labelcolor=RED)

    ax1.set_xticks(x)
    ax1.set_xticklabels(months, rotation=45, ha="right")
    ax1.set_title("收入逐月波动，但近5个月连续亏损", fontsize=13, fontweight="bold", pad=15)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

    fig.text(0.99, 0.01, "数据来源：利润表", ha="right", fontsize=8, color=GRAY)
    plt.tight_layout()

    out = _ensure_dir(output_dir) / "chart1_收入净利润.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    return str(out)


def chart2_cost_structure(income_df: pd.DataFrame, output_dir: str) -> str:
    """图2：成本费用结构堆叠图"""
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)

    months = income_df["month"].tolist()
    cogs = income_df["cogs"].tolist()
    admin = income_df["admin_exp"].tolist()
    selling = income_df["selling_exp"].tolist()

    x = range(len(months))
    ax.bar(x, cogs, color=BLUE, label="营业成本")
    ax.bar(x, admin, bottom=cogs, color=LIGHT_BLUE, label="管理费用")
    ax.bar(x, selling, bottom=[c + a for c, a in zip(cogs, admin)], color=RED, label="销售费用")

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=45, ha="right")
    ax.set_ylabel("金额（万元）")
    ax.set_title("销售费用占比逐月攀升，吞噬利润", fontsize=13, fontweight="bold", pad=15)
    ax.legend(loc="upper left")

    fig.text(0.99, 0.01, "数据来源：利润表", ha="right", fontsize=8, color=GRAY)
    plt.tight_layout()

    out = _ensure_dir(output_dir) / "chart2_成本费用结构.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    return str(out)


def chart3_balance_trend(balance_df: pd.DataFrame, output_dir: str) -> str:
    """图3：资产负债月度变动图"""
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)

    months = balance_df["month"].tolist()
    cash = balance_df["cash"].tolist()
    ap = balance_df["accounts_payable"].tolist()
    re_ = balance_df["retained_earnings"].tolist()
    total = balance_df["total_assets"].tolist()

    x = range(len(months))
    ax.plot(x, cash, color=BLUE, marker="o", linewidth=2, label="货币资金")
    ax.plot(x, ap, color=RED, marker="s", linewidth=2, label="应付账款")
    ax.plot(x, re_, color=GRAY, marker="^", linewidth=2, label="未分配利润")
    ax.axhline(y=0, color="black", linestyle="-", linewidth=0.5)

    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=45, ha="right")
    ax.set_ylabel("金额（万元）")
    ax.set_title("货币资金持续流出，应付账款与累计亏损反向攀升", fontsize=13, fontweight="bold", pad=15)
    ax.legend(loc="upper left")

    fig.text(0.99, 0.01, "数据来源：资产负债表", ha="right", fontsize=8, color=GRAY)
    plt.tight_layout()

    out = _ensure_dir(output_dir) / "chart3_资产负债趋势.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    return str(out)


def chart4_gross_margin(income_df: pd.DataFrame, output_dir: str) -> str:
    """图4：毛利率月度波动图"""
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)

    months = income_df["month"].tolist()
    rev = income_df["revenue"].tolist()
    cogs = income_df["cogs"].tolist()
    gm = [(r - c) / r * 100 if r > 0 else 0 for r, c in zip(rev, cogs)]

    x = range(len(months))
    colors = [RED if g < 20 else BLUE for g in gm]
    bars = ax.bar(x, gm, color=colors, alpha=0.8)

    ax.axhline(y=sum(gm)/len(gm) if gm else 0, color=GRAY, linestyle="--", label="平均毛利率")
    ax.set_xticks(x)
    ax.set_xticklabels(months, rotation=45, ha="right")
    ax.set_ylabel("毛利率（%）")
    ax.set_title("毛利率波动剧烈，部分月份转负", fontsize=13, fontweight="bold", pad=15)
    ax.legend()

    for i, v in enumerate(gm):
        ax.text(i, v + 2, f"{v:.0f}%", ha="center", fontsize=8)

    fig.text(0.99, 0.01, "数据来源：利润表", ha="right", fontsize=8, color=GRAY)
    plt.tight_layout()

    out = _ensure_dir(output_dir) / "chart4_毛利率波动.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    return str(out)


def chart5_key_metrics(result, output_dir: str) -> str:
    """图5：关键指标速览（仪表盘风格）"""
    km = result.key_metrics
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), dpi=150)

    total_rev = km.get("累计收入_万", 0)
    total_np = km.get("累计净利润_万", 0)
    end_cash = km.get("期末货币资金_万", 0)

    metrics = [
        ("累计收入", f"{total_rev:.0f}万", BLUE),
        ("累计净利润", f"{total_np:.0f}万", RED if total_np < 0 else BLUE),
        ("期末货币资金", f"{end_cash:.1f}万", BLUE),
    ]

    for ax, (label, value, color) in zip(axes, metrics):
        ax.text(0.5, 0.6, value, ha="center", va="center", fontsize=24, fontweight="bold", color=color)
        ax.text(0.5, 0.25, label, ha="center", va="center", fontsize=12, color=GRAY)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    fig.suptitle("关键数字速览", fontsize=14, fontweight="bold", y=0.95)
    plt.tight_layout()

    out = _ensure_dir(output_dir) / "chart5_关键指标.png"
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    return str(out)


def generate_all_charts(result, output_dir: str) -> dict:
    """生成全部5张图表，返回路径字典"""
    charts = {}
    # 没有财务数据时，跳过所有图表生成
    if result is None:
        return charts
    if result.monthly_income is not None and len(result.monthly_income) > 0:
        try:
            charts["chart1"] = chart1_revenue_profit(result.monthly_income, output_dir)
            charts["chart2"] = chart2_cost_structure(result.monthly_income, output_dir)
            charts["chart4"] = chart4_gross_margin(result.monthly_income, output_dir)
        except:
            pass  # 单张图生成失败不影响整体
    if result.monthly_balance is not None and len(result.monthly_balance) > 0:
        try:
            charts["chart3"] = chart3_balance_trend(result.monthly_balance, output_dir)
        except:
            pass
    try:
        charts["chart5"] = chart5_key_metrics(result, output_dir)
    except:
        pass
    return charts
