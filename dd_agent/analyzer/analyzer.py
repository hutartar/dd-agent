"""
财务分析器：三表自动提取 + 口径对齐 + 比率计算
所有计算三层留痕：原始数据→清洗→计算结果
"""
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings("ignore")


# ============ 数据结构 ============

@dataclass
class MonthlyIncome:
    """月度利润表数据"""
    month: str  # YYYYMM
    revenue: float = 0.0
    cogs: float = 0.0
    tax_surcharge: float = 0.0
    selling_exp: float = 0.0
    admin_exp: float = 0.0
    finance_exp: float = 0.0
    operating_profit: float = 0.0
    total_profit: float = 0.0
    income_tax: float = 0.0
    net_profit: float = 0.0
    source_file: str = ""
    source_sheet: str = ""
    is_cumulative: bool = False  # 年度报表：只有"本年累计"，没有当月数


@dataclass
class MonthlyBalance:
    """月度资产负债表数据"""
    month: str
    cash: float = 0.0
    accounts_receivable: float = 0.0
    fixed_assets: float = 0.0
    total_assets: float = 0.0
    accounts_payable: float = 0.0
    salary_payable: float = 0.0
    tax_payable: float = 0.0
    other_payable: float = 0.0
    total_liab: float = 0.0
    paid_in_capital: float = 0.0
    retained_earnings: float = 0.0
    total_equity: float = 0.0
    source_file: str = ""
    source_sheet: str = ""


@dataclass
class AnalysisResult:
    monthly_income: pd.DataFrame = None
    monthly_balance: pd.DataFrame = None
    key_metrics: Dict = field(default_factory=dict)
    caliber_notes: List[str] = field(default_factory=list)
    trace_log: List[str] = field(default_factory=list)


# ============ 科目映射 ============

INCOME_ITEMS = {
    "revenue":      ["一、营业收入", "营业收入"],
    "cogs":         ["减：营业成本", "营业成本"],
    "tax_surcharge": ["税金及附加"],
    "selling_exp":  ["销售费用"],
    "admin_exp":    ["管理费用"],
    "finance_exp":  ["财务费用"],
    "operating_profit": ["二、营业利润", "营业利润"],
    "total_profit": ["三、利润总额", "利润总额"],
    "income_tax":   ["减：所得税费用", "所得税费用", "所得税"],
    "net_profit":   ["四、净利润", "净利润（净亏损"],
}

BALANCE_ASSETS = {
    "cash":                  ["货币资金"],
    "accounts_receivable":   ["应收账款"],
    "fixed_assets":          ["固定资产"],
    "total_assets":          ["资产总计", "资产合计"],
}

BALANCE_LIAB = {
    "accounts_payable":  ["应付账款"],
    "salary_payable":    ["应付职工薪酬", "应付工资"],
    "tax_payable":       ["应交税费", "未交税金"],
    "other_payable":     ["其他应付款"],
    "total_liab":        ["负债合计", "负债总计"],
    "paid_in_capital":   ["实收资本", "股本"],
    "retained_earnings": ["未分配利润"],
    "total_equity":      ["所有者权益合计", "股东权益合计", "所有者权益总计"],
}


# ============ 主分析器 ============

class FinancialAnalyzer:
    """
    财务分析器
    用法:
        analyzer = FinancialAnalyzer()
        analyzer.load_files(file_list)  # 传入Excel文件路径列表
        result = analyzer.analyze_all()
    """

    def __init__(self):
        self.incomes: List[MonthlyIncome] = []
        self.balances: List[MonthlyBalance] = []
        self.result = AnalysisResult()
        self._unit = "元"  # 原始单位

    # ---------- 公开接口 ----------

    def load_files(self, file_paths: List[str]) -> "FinancialAnalyzer":
        """加载Excel文件列表，自动识别利润表和资产负债表"""
        for fp in file_paths:
            path = Path(fp)
            if not path.exists():
                self.result.trace_log.append(f"[跳过] 文件不存在: {fp}")
                continue
            self._process_file(path)
        return self

    def analyze_all(self) -> AnalysisResult:
        """执行全部分析"""
        self._build_dataframes()
        self._align_calibers()
        self._calc_metrics()
        return self.result

    # ---------- 文件识别与提取 ----------

    def _process_file(self, path: Path):
        """处理单个Excel文件，自动判断是利润表还是资产负债表"""
        try:
            xl = pd.ExcelFile(path)
        except Exception as e:
            self.result.trace_log.append(f"[错误] 无法打开 {path.name}: {e}")
            return

        for sheet in xl.sheet_names:
            try:
                df = pd.read_excel(path, sheet_name=sheet, header=None)
                if df.empty or df.shape[1] < 2:
                    continue
                # 判断是利润表还是资产负债表：sheet 名和表头都算
                if "利润表" in sheet or self._is_income_statement(df):
                    inc = self._parse_income_sheet(df, path.name, sheet)
                    if inc:
                        self.incomes.append(inc)
                elif "资产负债表" in sheet or self._is_balance_sheet(df):
                    bal = self._parse_balance_sheet(df, path.name, sheet)
                    if bal:
                        self.balances.append(bal)
            except Exception as e:
                self.result.trace_log.append(f"[跳过] {path.name}/{sheet}: {e}")

    def _is_income_statement(self, df: pd.DataFrame) -> bool:
        """判断sheet是否为利润表"""
        try:
            text = " ".join(df.iloc[:5, 0].dropna().astype(str).tolist())
            return "利润表" in text
        except (IndexError, KeyError):
            return False

    def _is_balance_sheet(self, df: pd.DataFrame) -> bool:
        """判断sheet是否为资产负债表"""
        try:
            text = " ".join(df.iloc[:5, 0].dropna().astype(str).tolist())
            return "资产负债表" in text
        except (IndexError, KeyError):
            return False

    # ---------- 利润表解析 ----------

    def _parse_income_sheet(self, df: pd.DataFrame, filename: str, sheet: str) -> Optional[MonthlyIncome]:
        """从利润表sheet中提取月度数据"""
        # 找月份
        month = self._extract_month_from_income(df, sheet, filename)
        if not month:
            return None

        inc = MonthlyIncome(month=month, source_file=filename, source_sheet=sheet)

        # 判断列位置：不同文件格式列位置不同
        # 格式A (2025.5-10): col3=本月金额, col4=本年累计
        # 格式B (2025.11-12, 2026.1-7): col2=本年累计, col3=本月金额
        col_monthly, col_ytd, is_cumulative = self._detect_income_columns(df)
        inc.is_cumulative = is_cumulative

        for i in range(len(df)):
            item_name = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
            for field_name, keywords in INCOME_ITEMS.items():
                if any(kw in item_name for kw in keywords):
                    val = self._to_float(df.iloc[i, col_monthly])
                    # 只在第一次赋值，后面的子科目不覆盖前面的有效值
                    if getattr(inc, field_name) == 0:
                        setattr(inc, field_name, val)
                    break

        self.result.trace_log.append(
            f"[利润表] {month} | 收入={inc.revenue:.0f} | 成本={inc.cogs:.0f} | "
            f"净利={inc.net_profit:.0f} | 来源={filename}/{sheet}"
        )
        return inc

    def _detect_income_columns(self, df: pd.DataFrame) -> Tuple[int, int, bool]:
        """返回 (取值列, 累计列, 是否只有累计)。只有"本年累计"时用它代替本月。"""
        for i in range(min(10, len(df))):
            monthly_col = None
            ytd_col = None
            for j in range(df.shape[1]):
                v = df.iloc[i, j]
                if pd.isna(v):
                    continue
                v_str = str(v)
                if "本月" in v_str and "金额" in v_str:
                    monthly_col = j
                elif "本年" in v_str and "累计" in v_str:
                    ytd_col = j
            if monthly_col is not None and ytd_col is not None:
                return monthly_col, ytd_col, False
            if ytd_col is not None:
                # 年度报表：只有累计列
                return ytd_col, ytd_col, True
        # 默认: 格式A (旧版xls)
        return 3, 4, False

    def _extract_month_from_income(self, df: pd.DataFrame, sheet_name: str, filename: str = "") -> str:
        """从sheet名、表格内容或文件名提取月份，返回YYYYMM格式"""
        import re
        # 先从sheet名提取
        m = re.search(r"(\d{4})[年\-_](\d{1,2})", sheet_name)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"

        # 从表格内容提取
        for i in range(min(5, len(df))):
            for j in range(min(5, df.shape[1])):
                val = str(df.iloc[i, j]) if pd.notna(df.iloc[i, j]) else ""
                m = re.search(r"(20\d{2})[\-年/](\d{1,2})", val)
                if m:
                    return f"{m.group(1)}{int(m.group(2)):02d}"

        # 从文件名提取（年度报表常见：财务报表_202412.xls）
        if filename:
            m = re.search(r"(20\d{2})(0[1-9]|1[0-2])(?!\d)", filename)
            if m:
                return f"{m.group(1)}{m.group(2)}"
        return ""

    # ---------- 资产负债表解析 ----------

    def _parse_balance_sheet(self, df: pd.DataFrame, filename: str, sheet: str) -> Optional[MonthlyBalance]:
        """从资产负债表sheet中提取月度数据"""
        month = self._extract_month_from_balance(df, sheet)
        if not month:
            return None

        bal = MonthlyBalance(month=month, source_file=filename, source_sheet=sheet)

        # 资产端: col2=期末, col3=年初
        # 负债端: col6=期末, col7=年初 (大多数格式)
        # 自动检测
        col_asset_end, col_liab_end = self._detect_balance_columns(df)

        for i in range(len(df)):
            # 资产端（左半边）
            asset_name = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
            for field_name, keywords in BALANCE_ASSETS.items():
                if any(kw in asset_name for kw in keywords):
                    val = self._to_float(df.iloc[i, col_asset_end])
                    setattr(bal, field_name, val)
                    break

            # 负债端（右半边）
            liab_name = str(df.iloc[i, col_liab_end - 2]).strip() if pd.notna(df.iloc[i, col_liab_end - 2]) else ""
            for field_name, keywords in BALANCE_LIAB.items():
                if any(kw in liab_name for kw in keywords):
                    val = self._to_float(df.iloc[i, col_liab_end])
                    setattr(bal, field_name, val)
                    break

        self.result.trace_log.append(
            f"[资产负债表] {month} | 货币资金={bal.cash:.0f} | "
            f"总资产={bal.total_assets:.0f} | 未分配利润={bal.retained_earnings:.0f}"
        )
        return bal

    def _detect_balance_columns(self, df: pd.DataFrame) -> Tuple[int, int]:
        """自动检测资产期末列和负债期末列"""
        for i in range(min(5, len(df))):
            asset_col = None
            liab_col = None
            for j in range(df.shape[1]):
                v = df.iloc[i, j]
                if pd.isna(v):
                    continue
                v_str = str(v)
                if "期末" in v_str and asset_col is None:
                    asset_col = j
                elif "期末" in v_str and asset_col is not None and j > asset_col:
                    liab_col = j
            if asset_col and liab_col:
                return asset_col, liab_col
        # 默认
        return 2, 6

    def _extract_month_from_balance(self, df: pd.DataFrame, sheet_name: str) -> str:
        """从资产负债表提取月份"""
        import re
        m = re.search(r"(\d{4})[\-年_](\d{1,2})", sheet_name)
        if m:
            return f"{m.group(1)}{int(m.group(2)):02d}"
        for i in range(min(5, len(df))):
            for j in range(min(8, df.shape[1])):
                val = str(df.iloc[i, j]) if pd.notna(df.iloc[i, j]) else ""
                m = re.search(r"(20\d{2})[\-年/](\d{1,2})", val)
                if m:
                    return f"{m.group(1)}{int(m.group(2)):02d}"
        return ""

    # ---------- 工具方法 ----------

    @staticmethod
    def _to_float(v) -> float:
        """安全转浮点数"""
        if pd.isna(v):
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace(",", "").replace("，", "").strip()
        try:
            return float(s)
        except:
            return 0.0

    # ---------- 对齐与计算 ----------

    def _build_dataframes(self):
        """把月度数据转成DataFrame"""
        if self.incomes:
            self.result.monthly_income = pd.DataFrame([vars(x) for x in self.incomes])
            self.result.monthly_income = self.result.monthly_income.sort_values("month").reset_index(drop=True)
            # 去重（同一月份可能从不同文件读到，保留第一个）
            self.result.monthly_income = self.result.monthly_income.drop_duplicates(subset="month", keep="first")

            # 全是年度累计报表：只保留最新一期，避免把累计值当月度值相加
            if (
                "is_cumulative" in self.result.monthly_income.columns
                and len(self.result.monthly_income) > 0
                and self.result.monthly_income["is_cumulative"].all()
            ):
                self.result.monthly_income = self.result.monthly_income.tail(1).reset_index(drop=True)
                self.result.trace_log.append("[口径] 年度累计报表，仅保留最新一期")

        if self.balances:
            self.result.monthly_balance = pd.DataFrame([vars(x) for x in self.balances])
            self.result.monthly_balance = self.result.monthly_balance.sort_values("month").reset_index(drop=True)
            self.result.monthly_balance = self.result.monthly_balance.drop_duplicates(subset="month", keep="first")

    def _align_calibers(self):
        """口径对齐检查"""
        notes = []
        notes.append(f"原始单位: {self._unit}")
        notes.append(f"利润表月份数: {len(self.incomes)}")
        notes.append(f"资产负债表月份数: {len(self.balances)}")

        # 检查单位一致性（万元 vs 元）
        if self.incomes:
            max_rev = max(x.revenue for x in self.incomes)
            if max_rev > 10000000:
                notes.append(f"⚠️ 最大收入{max_rev:.0f}，疑似单位为元")
            elif max_rev > 10000:
                notes.append(f"最大收入{max_rev:.0f}，单位待确认")

        self.result.caliber_notes = notes

    def _calc_metrics(self):
        """计算关键财务指标"""
        metrics = {}

        if self.result.monthly_income is not None and len(self.result.monthly_income) > 0:
            df = self.result.monthly_income
            # 转万元
            for col in ["revenue", "cogs", "admin_exp", "selling_exp", "net_profit"]:
                df[col + "_w"] = df[col] / 10000

            # 累计
            metrics["累计收入_万"] = df["revenue"].sum() / 10000
            metrics["累计净利润_万"] = df["net_profit"].sum() / 10000

            # 月均
            metrics["月均收入_万"] = df["revenue"].mean() / 10000
            metrics["月均净利润_万"] = df["net_profit"].mean() / 10000

            # 毛利率
            gross_margin = (df["revenue"] - df["cogs"]) / df["revenue"].replace(0, np.nan)
            metrics["平均毛利率"] = gross_margin.mean()
            metrics["毛利率标准差"] = gross_margin.std()

            # 费用率
            metrics["管理费用率"] = df["admin_exp"].sum() / df["revenue"].sum() if df["revenue"].sum() > 0 else 0
            metrics["销售费用率"] = df["selling_exp"].sum() / df["revenue"].sum() if df["revenue"].sum() > 0 else 0

            # 净利润连续为0的月数
            zero_np_months = (df["net_profit"].abs() < 1).sum()
            metrics["净利润为0的月数"] = int(zero_np_months)

        if self.result.monthly_balance is not None and len(self.result.monthly_balance) > 0:
            df = self.result.monthly_balance
            latest = df.iloc[-1]
            metrics["期末货币资金_万"] = latest["cash"] / 10000
            metrics["期末应付账款_万"] = latest["accounts_payable"] / 10000
            metrics["期末未分配利润_万"] = latest["retained_earnings"] / 10000
            metrics["期末总资产_万"] = latest["total_assets"] / 10000
            metrics["实收资本_万"] = latest["paid_in_capital"] / 10000

            # 货币资金变化
            if len(df) >= 2:
                cash_decline = (df.iloc[0]["cash"] - df.iloc[-1]["cash"]) / df.iloc[0]["cash"] * 100
                metrics["货币资金降幅_pct"] = cash_decline

        self.result.key_metrics = metrics
