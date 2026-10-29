"""
红旗规则引擎：纯Python确定性规则，不依赖LLM
规则定义在 config/red_flags.yaml
"""
import yaml
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class RedFlagHit:
    rule_id: str
    name: str
    severity: str  # high / medium / low
    category: str
    explanation: str
    actual_value: Any = None
    threshold: str = ""


class RedFlagEngine:
    """
    红旗规则引擎
    用法:
        engine = RedFlagEngine(analysis_result)
        hits = engine.scan()
    """

    def __init__(self, analysis_result, config_path: str = None,
                 extra_rules: List[Dict] = None, extra_vars: Dict[str, Any] = None):
        self.analysis = analysis_result
        self.rules = self._load_rules(config_path) + list(extra_rules or [])
        self.variables: Dict[str, Any] = {}
        self.extra_vars = extra_vars or {}

    def _load_rules(self, config_path: str = None) -> List[Dict]:
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "red_flags.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f).get("rules", [])

    def scan(self) -> List[RedFlagHit]:
        """执行所有规则扫描，返回命中的红旗列表"""
        self._extract_variables()

        hits = []
        for rule in self.rules:
            try:
                hit = self._evaluate_rule(rule)
                if hit:
                    hits.append(hit)
            except Exception:
                pass

        severity_order = {"high": 0, "medium": 1, "low": 2}
        hits.sort(key=lambda x: severity_order.get(x.severity, 3))
        return hits

    # ---------- 变量提取 ----------

    def _extract_variables(self):
        """从analysis_result中提取规则需要的中间变量；没有财务数据也能跑非财务规则。"""
        v = {}
        income = getattr(self.analysis, "monthly_income", None) if self.analysis else None
        balance = getattr(self.analysis, "monthly_balance", None) if self.analysis else None

        # === 资产负债表变量（取最后一期）===
        if balance is not None and len(balance) > 0:
            last = balance.iloc[-1]
            v["total_assets"] = float(last.get("total_assets", 0))
            v["paid_in_capital"] = float(last.get("paid_in_capital", 0))
            v["accounts_receivable"] = float(last.get("accounts_receivable", 0))
            v["accounts_payable"] = float(last.get("accounts_payable", 0))
            v["retained_earnings"] = float(last.get("retained_earnings", 0))
            v["cash"] = float(last.get("cash", 0))

            # 应付账款增长率
            if len(balance) >= 2:
                first_ap = float(balance.iloc[0].get("accounts_payable", 0))
                last_ap = float(last.get("accounts_payable", 0))
                if first_ap != 0:
                    v["accounts_payable_growth_pct"] = abs((last_ap - first_ap) / first_ap) * 100
                else:
                    v["accounts_payable_growth_pct"] = 999 if last_ap > 0 else 0

                # 应付账款连续上升月数
                ap_series = balance["accounts_payable"].astype(float).tolist()
                rising = 0
                for i in range(1, len(ap_series)):
                    rising = rising + 1 if ap_series[i] > ap_series[i-1] else 0
                v["accounts_payable_trending_up"] = rising

                # 货币资金下降
                cash_series = balance["cash"].astype(float).tolist()
                decline = 0
                for i in range(1, len(cash_series)):
                    decline = decline + 1 if cash_series[i] < cash_series[i-1] else 0
                v["cash_decline_months"] = decline

                peak = max(cash_series)
                v["cash_decline_pct"] = (peak - cash_series[-1]) / peak * 100 if peak > 0 else 0

            # 未分配利润连续恶化月数
            if balance is not None and len(balance) > 0:
                re_series = balance["retained_earnings"].astype(float).tolist()
                worsening = 0
                for i in range(1, len(re_series)):
                    worsening = worsening + 1 if re_series[i] < re_series[i-1] else 0
                v["retained_earnings_trending_worsening"] = worsening

        # === 利润表变量 ===
        if income is not None and len(income) > 0:
            np_series = income["net_profit"].astype(float).tolist()
            zero_streak = 0
            max_streak = 0
            for val in np_series:
                zero_streak = zero_streak + 1 if abs(val) < 1 else 0
                max_streak = max(max_streak, zero_streak)
            v["consecutive_zero_profit_months"] = max_streak
            v["total_zero_profit_months"] = sum(1 for x in np_series if abs(x) < 1)

            # 成本环比波动
            if len(income) >= 2:
                c_now = float(income.iloc[-1].get("cogs", 0))
                c_prev = float(income.iloc[-2].get("cogs", 0))
                r_now = float(income.iloc[-1].get("revenue", 0))
                r_prev = float(income.iloc[-2].get("revenue", 0))
                v["cogs_mom_change"] = abs((c_now - c_prev) / c_prev) if c_prev != 0 else (1.0 if c_now > 10000 else 0)
                v["revenue_mom_change"] = abs((r_now - r_prev) / r_prev) if r_prev != 0 else 0

            # 毛利率标准差
            rev = income["revenue"].astype(float)
            cogs = income["cogs"].astype(float)
            gm = (rev - cogs) / rev.replace(0, float("nan"))
            v["gross_margin_std_dev"] = float(gm.std()) if not gm.dropna().empty else 0
            v["gross_margin_avg"] = float(gm.mean()) if not gm.dropna().empty else 0
            v["len_income"] = len(income)

            # 新增：费用率
            total_rev = float(rev.sum())
            admin_exp = float(income.get("admin_exp", pd.Series([0])).sum())
            selling_exp = float(income.get("selling_exp", pd.Series([0])).sum())
            v["admin_expense_ratio"] = admin_exp / total_rev if total_rev > 0 else 0
            v["selling_expense_ratio"] = selling_exp / total_rev if total_rev > 0 else 0
            v["avg_monthly_revenue"] = total_rev / len(income)
            v["total_net_profit"] = float(income["net_profit"].sum())

            # 新增：收入月度波动
            rev_std = float(rev.std())
            v["revenue_cv"] = rev_std / v["avg_monthly_revenue"] if v["avg_monthly_revenue"] > 0 else 0

        # === 资产负债表新增变量 ===
        if balance is not None and len(balance) > 0:
            last = balance.iloc[-1]
            total_assets = float(last.get("total_assets", 0))
            # 简化计算：总负债 = 总资产 - 所有者权益
            equity = float(last.get("paid_in_capital", 0)) + float(last.get("retained_earnings", 0))
            total_liab = total_assets - equity
            v["total_liabilities"] = total_liab
            v["debt_to_asset_ratio"] = total_liab / abs(total_assets) if total_assets != 0 else 999

            # 其他应收/应付（如果有这个科目）
            v["other_receivables"] = float(last.get("other_receivables", 0))
            v["other_payables"] = float(last.get("other_payables", 0))
            v["inventory"] = float(last.get("inventory", 0))

        # 不猜注册资本和成立时间：由其他模块注入；缺失时相关规则自然不命中
        # （之前写死 800 万 / 17 个月，会把所有项目都误报一遍）

        # 行业指标（入住率、客流、对赌条款等）由调用方注入
        v.update(self.extra_vars)

        self.variables = v

    # ---------- 规则求值 ----------

    def _evaluate_rule(self, rule: Dict) -> RedFlagHit | None:
        trigger = rule.get("trigger", "")
        if not trigger:
            return None
        try:
            result = eval(trigger, {"__builtins__": {}}, self.variables)
        except Exception:
            return None

        if not result:
            return None

        return RedFlagHit(
            rule_id=rule["id"],
            name=rule["name"],
            severity=rule["severity"],
            category=rule.get("category", ""),
            explanation=self._build_explanation(rule),
            actual_value=self._get_actual_value(rule),
            threshold=rule.get("threshold", ""),
        )

    def _get_actual_value(self, rule: Dict):
        var_name = rule.get("trigger", "").split()[0] if rule.get("trigger") else ""
        return self.variables.get(var_name)

    def _build_explanation(self, rule: Dict) -> str:
        template = rule.get("explanation_template", rule.get("description", ""))
        try:
            return template.format(**self.variables)
        except (KeyError, IndexError):
            return rule.get("description", "")

    def summary(self) -> str:
        lines = ["=== 红旗引擎变量摘要 ==="]
        for k, v in sorted(self.variables.items()):
            lines.append(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")
        return "\n".join(lines)
