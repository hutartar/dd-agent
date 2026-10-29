"""
OTA渠道协议分析模块
提取佣金率、账期、独家条款等关键信息
"""
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class OTAAgreement:
    """OTA渠道协议"""
    file_name: str
    channel: str = ""              # 渠道名称（携程/美团/抖音/春秋国旅）
    commission_rate: str = ""     # 佣金率
    payment_terms: str = ""        # 账期/结算周期
    exclusive: str = ""            # 是否独家
    scope: str = ""                # 合作范围（客房/餐饮/全品类）
    ocr_text: str = ""            # OCR提取的原始文本
    llm_summary: str = ""          # LLM提取的关键条款


class OTAAnalyzer:
    """
    OTA渠道协议分析器
    用法:
        analyzer = OTAAnalyzer()
        analyzer.load_files(ota_files)
        summary = analyzer.get_summary()
    """

    def __init__(self, llm=None):
        self.agreements: List[OTAAgreement] = []
        self.llm = llm

    def load_files(self, file_paths: List[str]):
        """加载OTA协议文件"""
        for path in file_paths:
            p = Path(path)
            if not p.exists():
                continue

            # 判断渠道
            channel = self._detect_channel(p)

            agreement = OTAAgreement(
                file_name=p.name,
                channel=channel
            )

            # 提取PDF文本
            text = self._extract_text(p)
            agreement.ocr_text = text

            # 用LLM提取关键条款
            if self.llm and self.llm.available and text:
                agreement.llm_summary = self.llm.extract_ota_terms(text)

            self.agreements.append(agreement)

    def _detect_channel(self, path: Path) -> str:
        """从路径判断渠道"""
        path_str = str(path).lower()
        if "携程" in path_str or "ctrip" in path_str:
            return "携程"
        elif "美团" in path_str or "meituan" in path_str:
            return "美团"
        elif "抖音" in path_str or "douyin" in path_str:
            return "抖音"
        elif "春秋" in path_str:
            return "春秋国旅"
        else:
            return "其他"

    def _extract_text(self, path: Path) -> str:
        """从PDF提取文本"""
        if path.suffix != ".pdf":
            return ""

        # 先试试直接提取
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                text = ""
                for page in pdf.pages[:3]:
                    text += page.extract_text() or ""
                if len(text.strip()) > 50:
                    return text.strip()
        except:
            pass

        # 提取不到，试试OCR
        from ..ocr.extractor import OCRExtractor
        ocr = OCRExtractor()
        if ocr.available:
            return ocr.extract_pdf(path, max_pages=5)

        return ""

    def get_summary(self) -> str:
        """获取OTA协议摘要"""
        if not self.agreements:
            return "未找到OTA渠道协议文件"

        lines = []
        lines.append("### OTA渠道协议汇总")
        lines.append("")
        lines.append(f"共识别 {len(self.agreements)} 个OTA渠道协议")
        lines.append("")

        for agg in self.agreements:
            lines.append(f"**{agg.channel}：{agg.file_name}**")
            if agg.llm_summary:
                lines.append("")
                lines.append(agg.llm_summary)
            else:
                lines.append(f"  - OCR文本长度：{len(agg.ocr_text)}字")
            lines.append("")

        return "\n".join(lines)
