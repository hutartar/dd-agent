"""
酒店运营数据分析模块
解析PMS后台数据，计算出租率、ADR、RevPAR等酒店行业指标
"""
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict


@dataclass
class MonthlyOperationalData:
    """月度运营数据"""
    month: str  # YYYYMM
    total_revenue: float = 0.0      # PMS系统总收入
    room_revenue: float = 0.0       # 客房收入
    fnb_revenue: float = 0.0        # 餐饮收入
    occupied_nights: int = 0        # 间夜数
    avg_daily_rate: float = 0.0     # ADR平均房价
    occupancy_rate: float = 0.0     # 出租率%
    revpar: float = 0.0            # RevPAR
    channel_breakdown: Dict[str, float] = None  # 分渠道收入
    source_file: str = ""


class OperationalAnalyzer:
    """
    酒店运营数据分析器
    用法:
        analyzer = OperationalAnalyzer()
        analyzer.load_file(pms_file_path)
        monthly_data = analyzer.get_monthly_data()
    """

    def __init__(self):
        self.monthly_data: List[MonthlyOperationalData] = []

    def load_file(self, file_path: str):
        """加载PMS Excel文件（多sheet，每个sheet一个月）"""
        path = Path(file_path)
        if not path.exists():
            return

        xl = pd.ExcelFile(path)
        for sheet_name in xl.sheet_names:
            try:
                df = pd.read_excel(path, sheet_name=sheet_name, header=None)
                month_data = self._parse_sheet(df, sheet_name, path.name)
                if month_data:
                    self.monthly_data.append(month_data)
            except Exception as e:
                print(f"[运营数据] 解析{sheet_name}失败: {e}")

    def _parse_sheet(self, df: pd.DataFrame, sheet_name: str, filename: str) -> MonthlyOperationalData:
        """解析单个sheet（一个月的数据）"""
        # 找到数据开始的行（包含"营业日"的行）
        data_start_row = None
        for i in range(min(20, len(df))):
            if df.iloc[i, 0] == "营业日":
                data_start_row = i + 2  # 数据从表头下两行开始
                break

        if data_start_row is None:
            return None

        # 提取月度汇总（找最后几行的汇总数据）
        # 或者直接从每日数据计算
        monthly = MonthlyOperationalData(
            month=sheet_name.replace(".", ""),  # 2025.09 -> 202509
            source_file=filename
        )

        # 遍历每日数据
        total_rooms = 0
        total_room_rev = 0
        total_rev = 0
        total_fnb = 0
        occupancy_sum = 0
        days = 0

        for i in range(data_start_row, len(df)):
            row = df.iloc[i]
            date_val = row[0]

            # 判断是否是汇总行（不是日期格式）
            if not isinstance(date_val, str) or "-" not in str(date_val):
                continue

            try:
                # 提取数据
                revenue = float(row[2]) if pd.notna(row[2]) else 0
                occupied = int(row[3]) if pd.notna(row[3]) else 0
                adr = float(row[4]) if pd.notna(row[4]) else 0
                occupancy = float(row[5]) if pd.notna(row[5]) else 0
                room_rev = float(row[7]) if pd.notna(row[7]) else 0

                total_rev += revenue
                total_rooms += occupied
                total_room_rev += room_rev
                occupancy_sum += occupancy
                days += 1
            except (ValueError, TypeError):
                continue

        if days == 0:
            return None

        # 计算月度指标
        monthly.total_revenue = total_rev
        monthly.occupied_nights = total_rooms
        monthly.occupancy_rate = occupancy_sum / days if days > 0 else 0
        monthly.avg_daily_rate = total_room_rev / total_rooms if total_rooms > 0 else 0
        monthly.revpar = monthly.avg_daily_rate * monthly.occupancy_rate / 100

        return monthly

    def get_monthly_data_df(self) -> pd.DataFrame:
        """获取月度数据DataFrame"""
        if not self.monthly_data:
            return None

        data = []
        for m in self.monthly_data:
            data.append({
                "month": m.month,
                "pms总收入": m.total_revenue,
                "间夜数": m.occupied_nights,
                "平均房价ADR": m.avg_daily_rate,
                "出租率%": m.occupancy_rate,
                "RevPAR": m.revpar,
            })

        return pd.DataFrame(data)

    def get_summary(self) -> str:
        """获取运营数据摘要"""
        if not self.monthly_data:
            return "未找到PMS运营数据"

        df = self.get_monthly_data_df()
        avg_occ = df["出租率%"].mean()
        avg_adr = df["平均房价ADR"].mean()
        avg_revpar = df["RevPAR"].mean()
        total_rev = df["pms总收入"].sum()

        return (
            f"**运营数据摘要：**\n"
            f"- 数据覆盖：{len(self.monthly_data)}个月\n"
            f"- 平均出租率：{avg_occ:.1f}%\n"
            f"- 平均房价（ADR）：{avg_adr:.0f}元\n"
            f"- RevPAR（每间可售房收入）：{avg_revpar:.0f}元\n"
            f"- PMS系统总收入：{total_rev/10000:.1f}万元"
        )
