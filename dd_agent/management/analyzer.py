"""
管理层与股东背景调查模块
"""
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Shareholder:
    """股东"""
    name: str               # 股东名称
    ratio: float = 0.0      # 持股比例
    subscribed: float = 0.0  # 认缴出资
    paid_in: float = 0.0     # 实缴出资
    type: str = ""           # 股东类型（企业/自然人）


@dataclass
class Management:
    """管理层"""
    legal_rep: str = ""       # 法定代表人
    director: str = ""        # 执行董事
    supervisor: str = ""      # 监事
    general_manager: str = "" # 总经理


@dataclass
class CreditReport:
    """征信报告"""
    file_name: str
    report_type: str = ""     # 公司/个人
    ocr_text: str = ""        # OCR文本
    llm_summary: str = ""     # LLM提取摘要


@dataclass
class ChangeRecord:
    """工商变更记录"""
    change_date: str = ""     # 变更日期
    change_item: str = ""     # 变更项目
    before: str = ""          # 变更前
    after: str = ""           # 变更后


class ManagementAnalyzer:
    """
    管理层与股东分析器
    """

    def __init__(self, llm=None):
        self.shareholders: List[Shareholder] = []
        self.management = Management()
        self.legal_rep_change_history: List[str] = []
        self.llm = llm
        self.credit_reports: List[CreditReport] = []
        self.change_records: List[ChangeRecord] = []
        self.charter_summary: str = ""

    def load_files(self, files: List[Path]):
        """加载所有管理层相关文件"""
        for f in files:
            name = f.name.lower()
            if "征信" in name or "信用报告" in name:
                self._load_credit_report(f)
            elif "变更记录" in name:
                self._load_change_records(f)
            elif "工商信息" in name:
                self._load_business_info(f)
            elif "章程" in name:
                self._load_charter(f)

    def _load_credit_report(self, f: Path):
        """加载征信报告"""
        report = CreditReport(file_name=f.name)
        report.report_type = "个人" if "陈总" in f.name or "法人" in f.name else "公司"

        # 提取PDF文本
        text = self._extract_pdf_text(f)
        report.ocr_text = text

        # 用LLM提取摘要
        if self.llm and self.llm.available and text:
            report.llm_summary = self.llm.extract_credit_summary(text)

        self.credit_reports.append(report)

    def _load_change_records(self, f: Path):
        """加载工商变更记录"""
        try:
            import pandas as pd
            # 企查查导出的Excel前2行是标题，真正的列名在第2行（索引1）
            df = pd.read_excel(f, header=1)
            # 列名可能是Unnamed: 0, Unnamed: 1等，手动重命名
            if len(df.columns) >= 5:
                df.columns = ["序号", "变更日期", "变更项目", "变更前", "变更后", "数据来源"][:len(df.columns)]

            # 跳过前2行（标题和表头）
            df = df.iloc[1:].reset_index(drop=True)

            # 提取变更记录
            for _, row in df.iterrows():
                record = ChangeRecord()
                record.change_date = str(row.get("变更日期", ""))
                record.change_item = str(row.get("变更项目", ""))
                record.before = str(row.get("变更前", ""))[:50]  # 截断长文本
                record.after = str(row.get("变更后", ""))[:50]

                if record.change_date and record.change_item and record.change_item != "nan":
                    self.change_records.append(record)

            print(f"  解析到 {len(self.change_records)} 条变更记录")

        except Exception as e:
            print(f"读取变更记录失败: {e}")

    def _load_business_info(self, f: Path):
        """加载工商信息"""
        try:
            import pandas as pd
            df = pd.read_excel(f)
            # 提取法定代表人等信息
            for _, row in df.iterrows():
                key = str(row.iloc[0]) if len(row) > 0 else ""
                value = str(row.iloc[1]) if len(row) > 1 else ""
                if "法定代表人" in key:
                    self.management.legal_rep = value
        except Exception as e:
            print(f"读取工商信息失败: {e}")

    def _load_charter(self, f: Path):
        """加载公司章程（智能OCR：从目录精确计算正文页码，只OCR关键页）"""
        if f.suffix != ".pdf":
            return

        try:
            # 第一步：先OCR前2页（通常是档案查询清单/目录）
            intro_text = self._extract_pdf_text(f, max_pages=2)
            if not intro_text or len(intro_text) < 50:
                return

            # 第二步：分析目录结构，计算每个文件的页码范围
            lines = intro_text.split('\n')
            change_pages = 0
            charter_pages = 5  # 目录里说章程5页
            finance_pages = 0

            for line in lines:
                if '变更' in line and len(line) > 10:
                    numbers = [int(p) for p in line.split() if p.isdigit()]
                    if numbers:
                        change_pages = numbers[0]
                elif '财务报表' in line and len(line) > 10:
                    numbers = [int(p) for p in line.split() if p.isdigit()]
                    if numbers:
                        finance_pages = numbers[0]

            # 计算章程在整个PDF里的页码范围（1-indexed）
            charter_page_start = 2 + change_pages + 1
            charter_page_end = charter_page_start + charter_pages - 1

            print(f"📋 从目录分析：")
            print(f"   - 目录页：第1-2页")
            print(f"   - 变更记录：{change_pages}页")
            print(f"   - 章程：{charter_pages}页，从第{charter_page_start}页到第{charter_page_end}页")

            # 第三步：只OCR章程正文的那几页（0-indexed）
            from ..ocr.extractor import OCRExtractor
            ocr = OCRExtractor()
            if ocr.available:
                start_idx = charter_page_start - 1
                end_idx = charter_page_end - 1
                full_text = ocr.extract_pdf_pages(f, start_page=start_idx, end_page=end_idx)
                print(f"✅ 章程正文OCR完成，提取了{len(full_text)}字")
            else:
                full_text = ""
                print(f"⚠️ OCR不可用，跳过章程提取")

            # 第四步：用LLM提取关键条款
            if self.llm and self.llm.available and full_text:
                self.charter_summary = self.llm.extract_charter_terms(full_text)
                print(f"✅ 章程关键条款提取完成")

        except Exception as e:
            print(f"⚠️ 章程OCR出错（不影响整体流程）: {e}")
            import traceback
            traceback.print_exc()

    def _extract_pdf_text(self, f: Path, max_pages: int = 5) -> str:
        """从PDF提取文本"""
        if f.suffix != ".pdf":
            return ""

        # 先试试直接提取
        try:
            import pdfplumber
            with pdfplumber.open(f) as pdf:
                text = ""
                for page in pdf.pages[:max_pages]:
                    text += page.extract_text() or ""
                if len(text.strip()) > 50:
                    return text.strip()
        except:
            pass

        # 提取不到，试试OCR
        from ..ocr.extractor import OCRExtractor
        ocr = OCRExtractor()
        if ocr.available:
            return ocr.extract_pdf(f, max_pages=max_pages)

        return ""

    def load_charter(self, charter_pdf: Path):
        """
        从公司章程提取股东和管理层信息
        扫描版PDF无法自动提取，标记为需人工读取
        """
        self._load_charter(charter_pdf)

    def set_manual_info(self, legal_rep: str = "", shareholders: List[Shareholder] = None):
        """手动设置已知信息"""
        self.management.legal_rep = legal_rep
        if shareholders:
            self.shareholders = shareholders

    def get_summary(self) -> str:
        """输出管理层股东摘要"""
        lines = [
            "=" * 60,
            "👥 管理层与股东",
            "=" * 60,
            "",
        ]

        if self.management.legal_rep:
            lines.append(f"法定代表人：{self.management.legal_rep}")
        else:
            lines.append("法定代表人：需人工核实")
        lines.append("")

        lines.append("股东构成：")
        if self.shareholders:
            for s in self.shareholders:
                paid_ratio = s.paid_in / s.subscribed * 100 if s.subscribed > 0 else 0
                lines.append(f"  • {s.name}：持股{s.ratio:.1f}%（认缴{s.subscribed/10000:.0f}万，实缴{s.paid_in/10000:.0f}万，实缴率{paid_ratio:.0f}%）")
        else:
            lines.append("  需人工核实股东名册")

        lines.append("")
        return "\n".join(lines)
