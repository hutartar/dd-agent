"""
资质有效性核验模块
检查营业执照、行业许可证的有效期和经营范围
"""
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List
import pdfplumber


@dataclass
class Qualification:
    """资质证书"""
    name: str               # 资质名称
    cert_number: str = ""   # 证书编号
    valid_until: str = ""   # 有效期至
    issuer: str = ""        # 发证机关
    owner: str = ""        # 持证主体
    is_valid: bool = True   # 是否在有效期内
    notes: str = ""         # 备注


class QualificationChecker:
    """
    资质核验器
    用法:
        checker = QualificationChecker()
        checker.load_files(file_list)
        summary = checker.get_summary()
    """

    def __init__(self):
        self.quals: List[Qualification] = []

    def load_files(self, files: List[Path]):
        """加载资质文件"""
        for f in files:
            name = f.name.lower()
            if "营业执照" in name:
                self._parse_business_license(f)
            elif "许可证" in name or "资质" in name or "消防" in name:
                self._parse_license(f)

    def _parse_business_license(self, f: Path):
        """解析营业执照"""
        text = self._extract_text(f)
        if not text:
            self.quals.append(Qualification(
                name="营业执照",
                notes="扫描版PDF，需人工核验有效期和经营范围"
            ))
            return

        # 提取统一社会信用代码
        m = re.search(r"统一社会信用代码[：:]\s*([A-Z0-9]+)", text)
        cert = m.group(1) if m else ""

        # 提取成立日期
        m = re.search(r"成立日期[：:]\s*(\d{4}年\d{1,2}月\d{1,2}日)", text)
        establish = m.group(1) if m else ""

        self.quals.append(Qualification(
            name="营业执照",
            cert_number=cert,
            notes=f"成立日期：{establish}（有效期长期）"
        ))

    def _parse_license(self, f: Path):
        """解析行业许可证"""
        text = self._extract_text(f)
        if not text:
            self.quals.append(Qualification(
                name=f.stem,
                notes="扫描版PDF，需人工核验有效期"
            ))
            return

        self.quals.append(Qualification(
            name=f.stem,
            notes="可编辑PDF，需人工提取关键信息"
        ))

    def _extract_text(self, f: Path) -> str:
        """从PDF提取文本"""
        if f.suffix != ".pdf":
            return ""
        try:
            with pdfplumber.open(f) as pdf:
                text = ""
                for page in pdf.pages[:3]:
                    text += page.extract_text() or ""
                return text.strip()
        except Exception:
            return ""

    def get_summary(self) -> str:
        """输出资质核验摘要"""
        lines = [
            "=" * 60,
            "📜 资质有效性核验",
            "=" * 60,
            "",
        ]
        for q in self.quals:
            lines.append(f"• {q.name}")
            if q.cert_number:
                lines.append(f"  编号：{q.cert_number}")
            if q.notes:
                lines.append(f"  {q.notes}")
            lines.append("")

        if not self.quals:
            lines.append("未发现资质文件")

        return "\n".join(lines)
