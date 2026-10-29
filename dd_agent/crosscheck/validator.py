"""
交叉验证模块：收入三口径对比
利润表收入 vs 纳税申报收入 vs 银行流水转入
"""
import re
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass
import pdfplumber
import pandas as pd


@dataclass
class TaxDeclRecord:
    """纳税申报记录"""
    month: str        # YYYYMM
    sales: float      # 申报销售额（不含税）
    tax_payable: float  # 应纳税额
    source_file: str  # 来源文件
    period_type: str = "monthly"  # monthly / quarterly


class CrossChecker:
    """
    交叉验证器
    用法:
        checker = CrossChecker()
        checker.load_tax_returns(tax_pdf_files)
        checker.load_bank_flow(bank_excel_file)
        result = checker.compare_revenue(income_df)
    """

    def __init__(self):
        self.tax_records: List[TaxDeclRecord] = []
        self.bank_monthly: Dict[str, float] = {}  # month -> 转入总额

    # ---------- 纳税申报提取 ----------

    def load_tax_returns(self, pdf_files: List[str | Path]):
        """从增值税申报表PDF批量提取销售额"""
        for pdf_path in pdf_files:
            try:
                record = self._parse_tax_pdf(Path(pdf_path))
                if record:
                    self.tax_records.append(record)
            except Exception as e:
                print(f"[跳过] {Path(pdf_path).name}: {e}")

        self.tax_records.sort(key=lambda x: x.month)

    def _parse_tax_pdf(self, pdf_path: Path) -> TaxDeclRecord | None:
        """解析单个增值税申报表PDF（支持一般纳税人月报 + 小规模纳税人季报）"""
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text()

            # 判断是月报还是季报
            is_small_scale = "小规模纳税人" in text

            if is_small_scale:
                return self._parse_small_scale_pdf(text, pdf_path)
            else:
                return self._parse_general_pdf(text, pdf_path)

    def _parse_general_pdf(self, text: str, pdf_path: Path) -> TaxDeclRecord | None:
        """解析一般纳税人月报"""
        month = self._extract_tax_month_general(text)
        if not month:
            return None

        sales = self._extract_sales_general(text)
        tax = self._extract_tax_payable_general(text)

        return TaxDeclRecord(
            month=month,
            sales=sales,
            tax_payable=tax,
            source_file=pdf_path.name,
            period_type="monthly",
        )

    def _parse_small_scale_pdf(self, text: str, pdf_path: Path) -> TaxDeclRecord | None:
        """解析小规模纳税人季报（返回季度中间月份，销售额为季度合计）"""
        # 提取季度：匹配 "税款所属期：2025年10月1日至2025年12月31日"
        m = re.search(r"税款所属期：(\d{4})年(\d{1,2})月\d+日至\d{4}年(\d{1,2})月\d+日", text)
        if not m:
            return None

        year = m.group(1)
        start_month = int(m.group(2))
        end_month = int(m.group(3))

        # 提取第1栏销售额（本期数 = 季度合计）
        sales = self._extract_sales_small(text)

        # 提取应纳税额合计
        tax = self._extract_tax_payable_small(text)

        # 季度申报表返回3条记录（每个月一条，销售额=季度合计/3）
        # 这里先返回季度合计，后续对比时再处理
        quarter_mid_month = f"{year}{end_month:02d}"  # 用季度末月份标记

        return TaxDeclRecord(
            month=quarter_mid_month,
            sales=sales,  # 这是季度合计，不是月度
            tax_payable=tax,
            source_file=pdf_path.name,
            period_type="quarterly",
        )

    def _extract_tax_month_general(self, text: str) -> str:
        """从一般纳税人月报提取税款所属月份"""
        m = re.search(r"税款所属时间：自(\d{4})年(\d{1,2})月\d+日", text)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"
        return ""

    def _extract_sales_general(self, text: str) -> float:
        """提取一般纳税人月报的销售额（本月数）"""
        m = re.search(r"按适用税率计税销售额\s+1\s+([\d,\.]+)\s+([\d,\.]+)", text)
        if m:
            return float(m.group(1).replace(",", ""))
        return 0.0

    def _extract_tax_payable_general(self, text: str) -> float:
        """提取一般纳税人月报的应纳税额"""
        m = re.search(r"应纳税额合计\s+24=[\d\-+]+\s+([\d,\.]+)", text)
        if m:
            return float(m.group(1).replace(",", ""))
        return 0.0

    def _extract_sales_small(self, text: str) -> float:
        """提取小规模纳税人季报的销售额（本期数=季度合计）"""
        # 匹配 "（一）应征增值税不含税销售额（3%征收率） 1 0.00 2,765,311.22"
        m = re.search(r"应征增值税不含税销售额（3%征收率）\s+1\s+[\d,\.]+\s+([\d,\.]+)", text)
        if m:
            return float(m.group(1).replace(",", ""))
        return 0.0

    def _extract_tax_payable_small(self, text: str) -> float:
        """提取小规模纳税人季报的应纳税额合计"""
        m = re.search(r"应纳税额合计\s+20=15-16\s+[\d,\.]+\s+([\d,\.]+)", text)
        if m:
            return float(m.group(1).replace(",", ""))
        return 0.0

    # ---------- 收入对比 ----------

    def compare_revenue(self, income_df: pd.DataFrame) -> pd.DataFrame:
        """
        收入三口径对比
        支持月度对比（一般纳税人月报）+ 季度对比（小规模季报）
        """
        rows = []

        # 按period_type分类
        tax_monthly = {r.month: r.sales for r in self.tax_records if r.period_type == "monthly"}
        tax_quarterly = {r.month: r.sales for r in self.tax_records if r.period_type == "quarterly"}

        # 月度对比
        for _, row in income_df.iterrows():
            month = str(row["month"])
            pl_revenue = float(row["revenue"])
            tax_revenue = tax_monthly.get(month, None)

            if tax_revenue is not None and tax_revenue > 0:
                diff = pl_revenue - tax_revenue
                diff_pct = abs(diff) / tax_revenue * 100
                period_type = "月报"
            else:
                diff = None
                diff_pct = None
                period_type = None

            rows.append({
                "month": month,
                "期间类型": period_type,
                "利润表收入": round(pl_revenue, 2),
                "纳税申报收入": round(tax_revenue, 2) if tax_revenue else None,
                "差异": round(diff, 2) if diff is not None else None,
                "差异率%": round(diff_pct, 1) if diff_pct is not None else None,
            })

        # 季度对比：把利润表对应3个月的合计和季度申报对比
        for q_month, q_sales in tax_quarterly.items():
            year = q_month[:4]
            end_m = int(q_month[4:])
            q_months = [f"{year}{end_m-2:02d}", f"{year}{end_m-1:02d}", q_month]

            q_income = income_df[income_df["month"].astype(str).isin(q_months)]["revenue"].sum()

            diff = q_income - q_sales
            diff_pct = abs(diff) / q_sales * 100 if q_sales > 0 else 0

            rows.append({
                "month": f"{q_months[0]}~{q_month}",
                "期间类型": "季报",
                "利润表收入": round(q_income, 2),
                "纳税申报收入": round(q_sales, 2),
                "差异": round(diff, 2),
                "差异率%": round(diff_pct, 1),
            })

        return pd.DataFrame(rows)

    # ---------- 银行流水提取 ----------

    def load_bank_flow(self, excel_path: str | Path):
        """
        从银行流水Excel提取月度转入总额
        只统计经营性转入（POS、支付平台、OTA等）
        """
        df = pd.read_excel(excel_path, header=0)

        # 确保列存在
        if "交易时间" not in df.columns or "转入金额" not in df.columns:
            print("[错误] 银行流水格式不匹配")
            return

        # 解析交易时间，提取月份
        df["交易时间"] = pd.to_datetime(df["交易时间"], errors="coerce")
        df = df.dropna(subset=["交易时间"])
        df["month"] = df["交易时间"].dt.strftime("%Y%m")

        # 转换转入金额为数值
        df["转入金额"] = pd.to_numeric(
            df["转入金额"].astype(str).str.replace(",", "").replace("", "0"),
            errors="coerce"
        ).fillna(0)

        # 按月汇总
        monthly = df.groupby("month")["转入金额"].sum().to_dict()
        self.bank_monthly = monthly

    # ---------- 三口径对比 ----------

    def compare_cost_structure(self, cost_excel_path: str) -> pd.DataFrame:
        """
        成本费用结构验证：成本明细表提取
        提取每月租金、人工成本等主要成本项
        """
        df = pd.read_excel(cost_excel_path, header=1)

        result_rows = []

        # 提取每月租金
        rent_row = df[df.iloc[:, 0].astype(str).str.contains("酒店租金合计", na=False)]
        if not rent_row.empty:
            months = df.columns[1:13].tolist()
            rent_values = rent_row.iloc[0, 1:13].tolist()
            for m, v in zip(months, rent_values):
                try:
                    val = float(v)
                    if val > 0:
                        result_rows.append({
                            "项目": "酒店租金",
                            "月份": str(m).strip(),
                            "明细表金额": round(val, 2),
                        })
                except (ValueError, TypeError):
                    pass

        # 提取人工成本
        labor_row = df[df.iloc[:, 0].astype(str).str.contains("人工成本小计", na=False)]
        if not labor_row.empty:
            months = df.columns[1:13].tolist()
            labor_values = labor_row.iloc[0, 1:13].tolist()
            for m, v in zip(months, labor_values):
                try:
                    val = float(v)
                    if val > 0:
                        result_rows.append({
                            "项目": "人工成本",
                            "月份": str(m).strip(),
                            "明细表金额": round(val, 2),
                        })
                except (ValueError, TypeError):
                    pass

        return pd.DataFrame(result_rows)

    def compare_revenue_3way(self, income_df: pd.DataFrame) -> pd.DataFrame:
        """
        收入三口径对比：利润表 vs 纳税申报 vs 银行流水
        """
        rows = []

        tax_monthly = {r.month: r.sales for r in self.tax_records if r.period_type == "monthly"}
        tax_quarterly = {r.month: r.sales for r in self.tax_records if r.period_type == "quarterly"}

        # 月度对比
        for _, row in income_df.iterrows():
            month = str(row["month"])
            pl_revenue = float(row["revenue"])
            tax_revenue = tax_monthly.get(month, None)
            bank_inflow = self.bank_monthly.get(month, None)

            rows.append({
                "month": month,
                "利润表收入": round(pl_revenue, 2),
                "纳税申报收入": round(tax_revenue, 2) if tax_revenue else None,
                "银行转入": round(bank_inflow, 2) if bank_inflow else None,
                "税表差异率%": round(abs(pl_revenue - tax_revenue) / tax_revenue * 100, 1) if tax_revenue else None,
                "银行差异率%": round(abs(pl_revenue - bank_inflow) / bank_inflow * 100, 1) if bank_inflow else None,
            })

        return pd.DataFrame(rows)

    def summary(self) -> str:
        """输出对比摘要"""
        lines = [
            "=" * 60,
            "📊 收入三口径对比结果",
            "=" * 60,
            "",
            f"已加载纳税申报表: {len(self.tax_records)} 个月",
            "",
        ]

        lines.append(f"{'月份':<10} {'利润表收入':>12} {'纳税申报收入':>12} {'差异率':>10}")
        lines.append("-" * 60)

        for r in self.tax_records:
            lines.append(f"{r.month:<10} {'-':>12} {r.sales:>12,.0f} {'-':>10}")

        return "\n".join(lines)
