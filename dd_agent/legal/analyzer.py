"""
法律合规模块：提取合同/诉讼/处罚关键条款
对于扫描版PDF，自动调用OCR提取文本
"""
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
import pdfplumber

from ..ocr.extractor import OCRExtractor


@dataclass
class LeaseContract:
    """租赁合同"""
    file_name: str
    total_rent: float = 0.0          # 年租金
    lease_term: str = ""              # 租期
    rent_escalation: str = ""         # 租金递增条款
    deposit: float = 0.0              # 押金
    early_termination: str = ""       # 提前解约违约金
    landlord: str = ""                # 出租方
    is_editable: bool = False         # 是否可编辑PDF（否则需OCR）
    notes: str = ""                   # 备注
    ocr_text: str = ""                # OCR提取的原始文本


@dataclass
class Lawsuit:
    """未决诉讼"""
    file_name: str
    plaintiff: str = ""
    defendant: str = ""
    case_type: str = ""               # 案由
    amount: float = 0.0               # 涉案金额
    stage: str = ""                   # 当前阶段
    is_editable: bool = False
    notes: str = ""


@dataclass
class Penalty:
    """行政处罚"""
    file_name: str
    authority: str = ""               # 处罚机关
    reason: str = ""                  # 处罚事项
    amount: float = 0.0               # 罚款金额
    status: str = ""                  # 整改状态
    is_editable: bool = False


class LegalAnalyzer:
    """
    法律合规分析器
    用法:
        analyzer = LegalAnalyzer()
        analyzer.load_files(file_list)
        summary = analyzer.get_summary()
    """

    def __init__(self):
        self.leases: List[LeaseContract] = []
        self.lawsuits: List[Lawsuit] = []
        self.penalties: List[Penalty] = []
        self.unprocessed: List[str] = []   # 需OCR/人工处理的文件

    def load_files(self, files: List[Path]):
        """加载法律文件，自动分类处理"""
        for f in files:
            name = f.name.lower()

            if "租赁" in name or "租房" in name:
                self._process_lease(f)
            elif "诉讼" in name or "仲裁" in name:
                self._process_lawsuit(f)
            elif "处罚" in name or "行政处罚" in name:
                self._process_penalty(f)
            else:
                self.unprocessed.append(str(f))

    def _process_lease(self, f: Path):
        """处理租赁合同"""
        text = self._extract_text(f)
        lease = LeaseContract(file_name=f.name)
        lease.ocr_text = text or ""

        if not text:
            lease.is_editable = False
            lease.notes = "扫描版PDF，需OCR或人工核查"
            self.leases.append(lease)
            return

        lease.is_editable = True

        # 提取年租金（模糊匹配）
        # 找"租金"附近的数字+万/元
        rent_patterns = [
            r"年租金[：:]*\s*([\d,\.]+)\s*万",
            r"租金[金额]*[：:]*\s*([\d,\.]+)\s*万/年",
            r"每年租金[：:]*\s*([\d,\.]+)\s*万",
            r"租金[：:]*\s*([\d,\.]+)\s*万",
        ]
        for pattern in rent_patterns:
            m = re.search(pattern, text)
            if m:
                lease.total_rent = float(m.group(1).replace(",", "")) * 10000
                break

        # 提取租期
        term_patterns = [
            r"租期[限]*[：:]*\s*(\d+年.*?年)",
            r"租赁期限[：:]*\s*(\d+年.*?年)",
            r"期限[：:]*\s*(\d+年.*?年)",
        ]
        for pattern in term_patterns:
            m = re.search(pattern, text)
            if m:
                lease.lease_term = m.group(1)
                break

        # 提取出租方
        m = re.search(r"甲方[（(]出租方[）)][：:]*\s*(.+?)(?:\n|$)", text)
        if m:
            lease.landlord = m.group(1).strip()

        # 提取押金
        m = re.search(r"押金[：:]*\s*([\d,\.]+)\s*万", text)
        if m:
            lease.deposit = float(m.group(1).replace(",", "")) * 10000

        # 提取租金递增
        m = re.search(r"租金递增[：:]*\s*(.+?)(?:\n|$)", text)
        if m:
            lease.rent_escalation = m.group(1).strip()

        # 如果提取到了关键字段，标记为已提取
        if lease.total_rent > 0 or lease.lease_term:
            lease.notes = "OCR提取，建议人工复核数字准确性"
        else:
            lease.notes = "OCR已提取文字，但未识别到关键条款，建议人工查看"

        self.leases.append(lease)

    def _process_lawsuit(self, f: Path):
        """处理未决诉讼"""
        if f.suffix == ".doc" or f.suffix == ".docx":
            # Word文档，尝试读取
            lawsuit = Lawsuit(file_name=f.name)
            lawsuit.is_editable = True
            lawsuit.notes = "Word文档，需人工读取具体内容"
            self.lawsuits.append(lawsuit)
            return

        text = self._extract_text(f)
        lawsuit = Lawsuit(file_name=f.name)
        if not text:
            lawsuit.is_editable = False
            lawsuit.notes = "扫描版PDF，需OCR或人工核查"
        self.lawsuits.append(lawsuit)

    def _process_penalty(self, f: Path):
        """处理行政处罚"""
        text = self._extract_text(f)
        penalty = Penalty(file_name=f.name)

        if not text:
            penalty.is_editable = False
            penalty.status = "需人工核查"
            self.penalties.append(penalty)
            return

        penalty.is_editable = True
        penalty.authority = "企查查公示"
        # 尝试提取处罚事项
        if "行政处罚" in text:
            penalty.reason = "有行政处罚记录（详见原文）"
            penalty.status = "需人工核实具体内容"
        else:
            penalty.status = "公示报告中未发现处罚记录"

        self.penalties.append(penalty)

    def _extract_text(self, f: Path) -> str:
        """从PDF提取文本，扫描版自动调用OCR"""
        if f.suffix != ".pdf":
            return ""

        # 先试试直接提取
        try:
            with pdfplumber.open(f) as pdf:
                text = ""
                for page in pdf.pages[:3]:
                    text += page.extract_text() or ""
                if len(text.strip()) > 50:  # 提取到足够文本，直接返回
                    return text.strip()
        except Exception:
            pass

        # 提取不到，试试OCR
        ocr = OCRExtractor()
        if ocr.available:
            return ocr.extract_pdf(f, max_pages=5)

        return ""

    def get_summary(self) -> str:
        """输出法律合规摘要"""
        lines = [
            "=" * 60,
            "⚖️ 法律合规分析摘要",
            "=" * 60,
            "",
            f"租赁合同: {len(self.leases)} 份",
        ]

        for lease in self.leases:
            if lease.is_editable:
                lines.append(f"  • {lease.file_name}")
                lines.append(f"    年租金: {lease.total_rent/10000:.1f}万")
            else:
                lines.append(f"  • {lease.file_name} ⚠️ 扫描版，需人工核查")

        lines.append("")
        lines.append(f"未决诉讼: {len(self.lawsuits)} 件")
        for lawsuit in self.lawsuits:
            lines.append(f"  • {lawsuit.file_name}")
            if lawsuit.notes:
                lines.append(f"    {lawsuit.notes}")

        lines.append("")
        lines.append(f"行政处罚: {len(self.penalties)} 条")
        for p in self.penalties:
            lines.append(f"  • {p.file_name}")
            lines.append(f"    {p.status}")

        if self.unprocessed:
            lines.append("")
            lines.append(f"未分类法律文件: {len(self.unprocessed)} 个")
            for f in self.unprocessed:
                lines.append(f"  • {Path(f).name}")

        return "\n".join(lines)
