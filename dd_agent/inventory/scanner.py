"""
文件扫描与五大模块分拣
"""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional
from collections import defaultdict

from ..industry import IndustryPack, get_industry
from ..runtime_config import DATA_DIR

OVERRIDE_PATH = DATA_DIR / "classify_overrides.json"


def load_overrides() -> dict:
    try:
        with open(OVERRIDE_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def save_override(rel_path: str, category: str, module: str) -> dict:
    data = load_overrides()
    data[rel_path] = {"category": category, "module": module}
    OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OVERRIDE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


@dataclass
class FileInfo:
    path: Path
    size_mb: float
    file_type: str  # excel/pdf/doc/image/ppt/other
    module: str = ""  # financial/business/legal/qualification/management/other
    category: str = ""  # 更细的子分类：利润表/资产负债表/银行流水/合同等
    priority: str = ""  # P0/P1/P2
    is_income_statement: bool = False  # 是否利润表
    is_balance_sheet: bool = False     # 是否资产负债表
    reason: str = ""                   # 未分类原因（给前端看）
    converted_path: str = ""           # 老版 .doc 自动转换出的 .docx 缓存路径
    is_scanned: bool = False           # 扫描版 PDF（没有文字层，只能 OCR）
    importance: int = 0                # 重要性 0-100，决定 OCR 预算优先给谁


class FileScanner:
    """目录扫描与文件分类"""

    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".svn", ".idea", ".vscode", ".DS_Store"}

    TYPE_MAP = {
        ".xls": "excel", ".xlsx": "excel", ".csv": "excel",
        ".doc": "doc", ".docx": "doc",
        ".pdf": "pdf",
        ".ppt": "ppt", ".pptx": "ppt",
        ".jpg": "image", ".jpeg": "image", ".png": "image",
    }

    # 子分类关键词（按优先级排序，先匹配先归类）
    # 覆盖行业：酒店/民宿/餐饮/景区/文旅小镇/旅行社/旅游项目
    CATEGORY_RULES = [
        # === 财务真实性 ===
        ("利润表", ["利润表", "损益表", "利润"]),
        ("资产负债表", ["资产负债表", "资产负债"]),
        ("现金流量表", ["现金流量表", "现金流"]),
        ("成本费用明细", ["成本费用", "成本结构", "费用明细", "租金核算", "租金成本", "营业成本", "运营成本"]),
        ("收入流水", ["收入流水", "收入明细", "营业收入", "营收明细"]),
        ("银行流水", ["银行流水", "对账单", "账户明细", "支付凭证", "租金支付", "银行回单", "流水"]),
        ("纳税申报", ["增值税申报", "纳税申报", "申报表", "报税"]),
        ("完税证明", ["完税证明", "税收完税", "完税"]),
        ("租金评估", ["租金评估", "租金咨询", "租金估价", "租金测算"]),
        ("票务收入", ["门票", "票务", "售票", "票款"]),
        ("审计报告", ["审计报告", "审字", "审报告", "审计"]),

        # === 业务可持续性 ===
        # PMS 只保留酒店特有指标；客流/核销/停车场/舆情属于文旅，放下一行
        ("PMS运营数据", ["pms", "后台系统", "入住率", "出租率", "revpar", "adr", "客房"]),
        ("景区运营数据", ["游客量", "接待人数", "景区数据", "旅游人次", "客流量", "客流", "游客分析", "游客系统", "核销", "停车场", "舆情监测", "监控系统"]),
        ("OTA渠道协议", ["ota", "携程", "美团", "抖音", "春秋国旅", "渠道协议", "飞猪", "马蜂窝", "去哪儿"]),
        ("商业计划书", ["商业计划", "项目介绍", "项目方案", "融资计划", "项目白皮书", "白皮书", "项目终稿", "酒店终稿", "酒店方案", "项目建议书", "可研报告", "可行性研究", "收益率测算", "工程预算"]),
        ("项目评审材料", ["评审会", "评审材料", "尽调评审", "上会材料", "投决会", "立项报告", "立项批复", "百千万工程"]),
        ("品牌合作资料", ["品牌手册", "加盟手册", "品牌介绍", "品牌合作", "连锁品牌", "加盟政策"]),
        ("物业/景区资料", ["物业基本情况", "物业介绍", "物业概况", "景区介绍", "景区概况", "项目概况", "度假别墅", "民宿介绍"]),
        ("规划设计资料", ["规划", "设计方案", "建筑方案", "景观设计", "文旅规划", "景区规划"]),

        # === 法律合规 ===
        ("租赁合同", ["租赁合同", "租赁协议", "场地租赁", "房屋租赁"]),
        ("重大合同", ["合同", "租地合同", "建厂合同", "委托协议", "授权委托", "合作协议", "服务合同", "采购合同", "投资协议", "合资", "增资"]),
        ("公司章程", ["公司章程", "章程", "章程修正案", "章程修订"]),
        ("未决诉讼", ["诉讼", "仲裁", "纠纷", "起诉状", "起诉状", "起诉书"]),
        ("行政处罚", ["行政处罚", "处罚记录", "罚款", "罚没", "责令整改"]),
        ("对外担保", ["担保", "保证合同", "连带担保"]),
        ("土地相关文件", ["土地用途", "土地性质", "土地出让", "土地资料", "土地证", "不动产权证", "房产证", "房屋所有权"]),
        ("特许经营", ["特许经营", "特许经营权", "特许协议"]),

        # === 资质有效性 ===
        ("营业执照", ["营业执照", "执照"]),
        ("特种行业许可", ["特种经营", "特种行业", "特种许可", "特种行业许可证"]),
        ("消防验收", ["消防", "消防安全", "二消", "消防工程", "消防验收", "消防合格证"]),
        ("卫生许可", ["卫生许可", "卫生证", "卫生许可证"]),
        ("公司主体", ["公司主体", "工商信息", "公司基本情况", "工商档案", "有限公司", "酒店管理公司"]),
        ("旅游相关资质", ["旅行社业务经营许可证", "旅游经营", "景区经营资质", "文物保护资质"]),
        ("食品经营许可", ["食品经营", "餐饮服务许可", "食品卫生"]),

        # === 管理层与股东 ===
        ("股东名册", ["股东名册", "股东清单", "股东出资"]),
        ("股权架构", ["股权架构", "股权结构", "股权图", "股权穿透"]),
        ("工商变更", ["变更记录", "工商信息", "企查查", "工商变更", "变更记录"]),
        ("高管简历", ["高管", "简历", "履历", "实控人", "实际控制人", "法人资料", "董事长", "总经理"]),
        ("征信报告", ["征信", "信用报告", "信用报告", "企业征信"]),
    ]

    # 模块映射（子分类 → 五大模块）
    CATEGORY_TO_MODULE = {
        # 财务真实性
        "利润表": "financial",
        "资产负债表": "financial",
        "现金流量表": "financial",
        "成本费用明细": "financial",
        "收入流水": "financial",
        "银行流水": "financial",
        "纳税申报": "financial",
        "完税证明": "financial",
        "租金评估": "financial",
        "票务收入": "financial",
        "审计报告": "financial",
        
        # 业务可持续性
        "PMS运营数据": "business",
        "景区运营数据": "business",
        "OTA渠道协议": "business",
        "商业计划书": "business",
        "项目评审材料": "business",
        "品牌合作资料": "business",
        "物业/景区资料": "business",
        "规划设计资料": "business",
        
        # 法律合规
        "租赁合同": "legal",
        "重大合同": "legal",
        "公司章程": "legal",
        "未决诉讼": "legal",
        "行政处罚": "legal",
        "对外担保": "legal",
        "土地相关文件": "legal",
        "特许经营": "legal",
        
        # 资质有效性
        "营业执照": "qualification",
        "特种行业许可": "qualification",
        "消防验收": "qualification",
        "卫生许可": "qualification",
        "公司主体": "qualification",
        "旅游相关资质": "qualification",
        "食品经营许可": "qualification",
        
        # 管理层与股东
        "股东名册": "management",
        "股权架构": "management",
        "工商变更": "management",
        "高管简历": "management",
        "征信报告": "management",
    }

    def __init__(self, root: str | Path, industry: str = None):
        self.root = Path(root)
        self.industry: IndustryPack = get_industry(industry) if not isinstance(industry, IndustryPack) else industry
        self.files: List[FileInfo] = []
        self.total_files = 0
        self.total_size_mb = 0
        self.overrides = load_overrides()
        # 行业包里的分类词优先，再走通用规则
        self.rules = []
        for item in self.industry.categories:
            self.rules.append({
                "category": item.get("category"),
                "keywords": item.get("keywords") or [],
                "module": item.get("module"),
            })
        for category_name, keywords in self.CATEGORY_RULES:
            self.rules.append({"category": category_name, "keywords": keywords, "module": None})

    def scan(self) -> "FileScanner":
        """递归扫描目录"""
        for dirpath, dirnames, filenames in os.walk(self.root):
            # 跳过噪声目录
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
            for fn in filenames:
                if fn.startswith("~$") or fn.startswith("."):
                    continue
                p = Path(dirpath) / fn
                ext = p.suffix.lower()
                ftype = self.TYPE_MAP.get(ext, "other")
                size_mb = p.stat().st_size / 1024 / 1024

                finfo = FileInfo(
                    path=p,
                    size_mb=round(size_mb, 2),
                    file_type=ftype,
                )
                self._classify(finfo)
                self.files.append(finfo)

        self.total_files = len(self.files)
        self.total_size_mb = round(sum(f.size_mb for f in self.files), 1)
        
        # === 第二步：内容快速校验，纠正错分 ===
        self._content_validate()
        
        return self

    def _content_validate(self):
        """根据文件内容快速校验，纠正明显错分（轻量版，不跑OCR）"""
        # 五大类兜底关键词：不管文件名是什么，只要内容里有这些词，就归到对应模块
        MODULE_KEYWORDS = {
            "financial": ["利润表", "资产负债表", "营业收入", "净利润", "本月数", "本年累计", "审计报告", "审字", "财务报送", "纳税申报", "增值税", "银行流水", "收益率", "工程预算", "成本费用", "完税", "测算"],
            "legal": ["甲方", "乙方", "租赁期限", "租金", "押金", "合同", "协议", "诉讼", "仲裁", "行政处罚", "担保", "土地", "不动产权证", "立项批复", "合资", "增资", "投资协议", "房产证", "房屋所有权"],
            "qualification": ["营业执照", "统一社会信用代码", "有效期", "特种行业", "消防验收", "卫生许可", "食品经营", "许可证"],
            "management": ["股东", "股权", "持股", "工商变更", "法人", "章程", "征信", "高管", "简历", "身份证"],
            "business": ["项目", "规划", "方案", "商业计划", "白皮书", "运营数据", "入住率", "出租率", "游客", "景区", "OTA", "携程", "美团", "立项", "百千万工程", "项目简介", "停车场", "核销", "监控", "系统"]
        }

        total = len(self.files)
        import time as _time
        started = _time.time()
        budget = 90  # 内容校验总预算（秒），到点就停，剩余文件只按文件名分类
        for index, f in enumerate(self.files):
            if _time.time() - started > budget:
                print(f"  内容校验超过 {budget} 秒，剩余文件只按文件名/目录分类")
                break
            if index and index % 10 == 0:
                print(f"  内容校验 {index}/{total}...")

            # 视频文件一律保持未分类，不处理
            if f.file_type == "video":
                f.module = "other"
                f.category = "视频文件（未处理）"
                continue

            ext = f.path.suffix.lower()

            # 只有大 Excel 跳过内容校验（openpyxl 解大表很慢）；
            # PDF 走 PDFium 懒加载，读第 1 页与文件体积无关，不看大小
            if f.size_mb > 20 and ext in (".xls", ".xlsx"):
                continue
            content_text = ""
            
            # Excel文件：读前10行表头（很快，1秒内）
            if ext in [".xls", ".xlsx"]:
                try:
                    import pandas as pd
                    df = pd.read_excel(f.path, nrows=10)
                    content_text = " ".join([str(c) for c in df.columns]).lower()
                except:
                    pass
            
            # Word文件：读前20段（很快，1秒内，不跑OCR）
            elif ext == ".docx":
                try:
                    from docx import Document
                    doc = Document(f.path)
                    content_text = "\n".join([p.text for p in doc.paragraphs[:20]]).lower()
                except Exception:
                    content_text = ""

            # RTF：去掉控制字后当纯文本读
            elif ext == ".rtf":
                try:
                    raw = f.path.read_text(encoding="utf-8", errors="ignore")
                    import re as _re
                    text = _re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", raw)
                    text = text.replace("{", " ").replace("}", " ")
                    content_text = text.lower()
                except Exception:
                    content_text = ""

            # 老版 .doc：先自动转 docx 再读；转不了就留未分类并给出原因
            elif ext == ".doc":
                try:
                    from ..extractor.doc_convert import convert_doc
                    converted = convert_doc(f.path)
                    if converted:
                        f.converted_path = str(converted)
                        from docx import Document
                        doc = Document(str(converted))
                        content_text = "\n".join([p.text for p in doc.paragraphs[:20]]).lower()
                except Exception:
                    content_text = ""
            
            # PDF文件：PDFium 提取文本。文字版不管多大都便宜；扫描版提取为空也只是空跑
            elif ext == ".pdf":
                try:
                    import pypdfium2 as pdfium
                    pdf = pdfium.PdfDocument(str(f.path))
                    try:
                        pages = len(pdf)
                        if pages and pages <= 100:
                            # 页数不多：全文提取，文字版通常毫秒级，信息不丢
                            parts = []
                            for i in range(pages):
                                parts.append(pdf[i].get_textpage().get_text_range() or "")
                            content_text = "\n".join(parts).lower()
                            if not content_text.strip():
                                f.is_scanned = True  # 有页但没文字层，是扫描件
                        elif pages:
                            # 超过 100 页：只读前 3 页，避免极端文档拖时间
                            parts = [pdf[i].get_textpage().get_text_range() or "" for i in range(3)]
                            content_text = "\n".join(parts).lower()
                            if not content_text.strip():
                                f.is_scanned = True
                    finally:
                        pdf.close()
                except Exception:
                    try:
                        import pdfplumber
                        with pdfplumber.open(str(f.path)) as pdf:
                            if pdf.pages:
                                content_text = (pdf.pages[0].extract_text() or "").lower()
                    except Exception:
                        pass
            
            # 大类兜底：只要内容里有对应模块的关键词，就纠正分类
            # 不管之前文件名匹配到什么，内容优先
            for module, keywords in MODULE_KEYWORDS.items():
                if any(k.lower() in content_text for k in keywords):
                    f.module = module
                    # 如果之前是未分类，给个合理的category
                    if f.category == "未分类":
                        f.category = f"内容识别-{module}"
                    break

        # === 轻量OCR兜底：只对还是未分类的图片/扫描版PDF跑OCR，按重要性分配预算 ===
        print("轻量OCR兜底分类（只处理未分类图片/扫描PDF）...")
        try:
            from ..ocr.extractor import OCRExtractor
            ocr = OCRExtractor()
            if ocr.available:
                candidates = [f for f in self.files if f.module == "other"]
                candidates.sort(key=lambda x: x.importance, reverse=True)
                limit = 10
                if len(candidates) > limit:
                    print(f"  未分类 {len(candidates)} 个，按重要性 OCR 前 {limit} 个")
                for index, f in enumerate(candidates[:limit]):
                    print(f"  OCR {index + 1}/{min(len(candidates), limit)}: {f.path.name[:40]}（重要性 {f.importance}）")
                    ext = f.path.suffix.lower()
                    ocr_text = ""
                    # 图片：OCR读前1张
                    if ext in [".jpg", ".jpeg", ".png"]:
                        try:
                            import numpy as np
                            from PIL import Image
                            img = Image.open(f.path)
                            result = ocr._get_paddleocr().ocr(np.array(img), cls=True)
                            if result and result[0]:
                                ocr_text = " ".join([line[1][0] for line in result[0]]).lower()
                        except:
                            pass
                    # 扫描版PDF：多读两页判断类型；文字版读 1 页就够
                    elif ext == ".pdf":
                        try:
                            pages = 3 if f.is_scanned else 1
                            ocr_text = ocr.extract_pdf(f.path, max_pages=pages).lower()
                        except:
                            pass
                    # 用OCR结果大类匹配
                    for module, keywords in MODULE_KEYWORDS.items():
                        if any(k.lower() in ocr_text for k in keywords):
                            f.module = module
                            f.category = f"OCR识别-{module}"
                            break
        except:
            pass  # OCR失败就保持未分类

        # 标记未分类原因：报告里要能解释"为什么没分"
        for f in self.files:
            if f.module != "other":
                continue
            ext = f.path.suffix.lower()
            if ext in (".mp4", ".avi", ".mov", ".mkv", ".wmv"):
                f.reason = "视频文件，当前版本不解析"
            elif ext == ".doc":
                f.reason = "老版 .doc，读取失败时可另存为 .docx 重试"
            elif ext in (".jpg", ".jpeg", ".png"):
                f.reason = "图片/截图未识别出业务关键词"
            elif ext == ".pdf":
                f.reason = "扫描版 PDF 未提取到可识别文字"
            else:
                f.reason = "文件名、目录、内容均未命中分类词"

        # 重要性评分：OCR 预算和"深读"建议都按这个排序
        for f in self.files:
            f.importance = self._score_importance(f)

    def _classify(self, f: FileInfo):
        """对单个文件做分类：人工修正 > 行业词 > 通用词 > 目录先验"""
        try:
            rel = f.path.relative_to(self.root)
        except ValueError:
            rel = Path(f.path.name)
        rel_posix = rel.as_posix()

        # 人工修正优先（报告页点过"改分类"的文件）
        override = self.overrides.get(rel_posix)
        if override:
            f.category = override.get("category") or "未分类"
            f.module = override.get("module") or "other"
            f.priority = self._guess_priority(f)
            return

        # 目录和文件名都参与匹配：目录先验往往比文件名更可靠
        search_text = " ".join(rel.parts).lower()

        matched = None
        for rule in self.rules:
            if any(str(kw).lower() in search_text for kw in rule["keywords"]):
                matched = rule
                break

        category = (matched or {}).get("category") or "未分类"
        f.category = category
        f.module = (matched or {}).get("module") or self.CATEGORY_TO_MODULE.get(category, "other")
        f.priority = self._guess_priority(f)

        # Excel文件二次校验：读sheet名判断是否利润表/资产负债表
        if f.file_type == "excel" and f.category in ("利润表", "资产负债表", "未分类"):
            self._verify_excel_sheets(f)

    def _verify_excel_sheets(self, f: FileInfo):
        """读取Excel的sheet名，二次校验是否为财务报表"""
        try:
            import pandas as pd
            xl = pd.ExcelFile(f.path)
            sheet_names = " ".join(xl.sheet_names).lower()

            has_income = "利润表" in sheet_names or "损益表" in sheet_names
            has_balance = "资产负债表" in sheet_names

            if has_income:
                f.is_income_statement = True
            if has_balance:
                f.is_balance_sheet = True

            # 如果之前是未分类，根据内容更新category和module
            if f.category == "未分类":
                if has_income and has_balance:
                    f.category = "财务报表（两表合一）"
                    f.module = "financial"
                elif has_income:
                    f.category = "利润表"
                    f.module = "financial"
                elif has_balance:
                    f.category = "资产负债表"
                    f.module = "financial"

            # 财务报表类自动升级为P0
            if has_income or has_balance:
                f.priority = "P0"
        except Exception:
            pass  # 读失败就保持原分类

    def _guess_priority(self, f: FileInfo) -> str:
        """猜测处理优先级"""
        # P0: 核心财务报表 + 核心法律文件
        if f.is_income_statement or f.is_balance_sheet:
            return "P0"
        if f.category in ("租赁合同", "公司章程", "未决诉讼", "行政处罚"):
            return "P0"
        # P1: 其他重要文件
        if f.category in ("银行流水", "纳税申报", "完税证明", "PMS运营数据",
                          "营业执照", "特种行业许可", "股东名册", "工商变更"):
            return "P1"
        # P2: 其余
        return "P2"

    # 文件名里出现这些词，说明是"说明/承诺/担保"类关键文件
    IMPORTANT_WORDS = ["说明", "承诺", "担保", "还款", "补充", "协议", "判决", "处罚", "批复", "许可", "情况"]
    CORE_CATEGORIES = {
        "利润表", "资产负债表", "租赁合同", "公司章程",
        "未决诉讼", "行政处罚", "营业执照", "银行流水", "纳税申报",
    }

    def _score_importance(self, f: FileInfo) -> int:
        """给文件打重要性分（0-100），OCR 预算优先给高分文件。"""
        score = 0
        name = f.path.name
        if any(word in name for word in self.IMPORTANT_WORDS):
            score += 25
        if f.category in self.CORE_CATEGORIES:
            score += 30
        if f.priority == "P0":
            score += 15
        if f.is_scanned:
            score += 5
        if 1 <= f.size_mb <= 50:
            score += 5
        return min(score, 100)

    # ---------- 汇总方法 ----------

    def get_financial_excels(self) -> List[Path]:
        """获取所有财务报表Excel路径（利润表+资产负债表）"""
        return [f.path for f in self.files if f.is_income_statement or f.is_balance_sheet]

    def group_by_module(self) -> Dict[str, List[FileInfo]]:
        """按五大模块分组"""
        result = defaultdict(list)
        for f in self.files:
            result[f.module].append(f)
        return dict(result)

    def group_by_category(self) -> Dict[str, List[FileInfo]]:
        """按子分类分组"""
        result = defaultdict(list)
        for f in self.files:
            result[f.category].append(f)
        return dict(result)

    def summary(self) -> str:
        """输出完整盘点摘要"""
        lines = [
            f"📁 {self.root.name}/",
            f"   共 {self.total_files} 个文件, {self.total_size_mb} MB",
            "",
        ]

        by_module = self.group_by_module()
        module_names = {
            "financial": "📊 财务真实性",
            "business": "🏨 业务可持续性",
            "legal": "⚖️ 法律合规",
            "qualification": "📜 资质有效性",
            "management": "👤 管理层与股东",
            "other": "❓ 未分类",
        }

        # 核心模块：只显示分类统计 + P0文件
        for mod in ["financial", "business", "legal", "qualification", "management"]:
            files = by_module.get(mod, [])
            if not files:
                continue
            lines.append(f"{module_names.get(mod, mod)} ({len(files)}个文件)")

            by_cat = defaultdict(list)
            for f in files:
                by_cat[f.category].append(f)

            for cat, cat_files in sorted(by_cat.items()):
                lines.append(f"  ├─ {cat}: {len(cat_files)}个")

            # 列出P0优先级文件（会被深度处理的）
            p0_files = [f for f in files if f.priority == "P0"]
            if p0_files:
                lines.append(f"  └─ 🔴 P0深读文件:")
                for f in p0_files:
                    rel = f.path.name
                    lines.append(f"      • {rel}")
            lines.append("")

        # 未分类：全部列出来，说明"看到了但暂未处理"
        other_files = by_module.get("other", [])
        if other_files:
            lines.append(f"{module_names['other']} ({len(other_files)}个文件)")
            lines.append(f"   ⚠️ 已扫描到以下文件，但暂未纳入分析流程：")
            for f in other_files:
                rel = f.path.relative_to(self.root)
                lines.append(f"      • {rel}")
            lines.append("")

        # 总结：多少文件会被深度处理
        p0_count = sum(1 for f in self.files if f.priority == "P0")
        p1_count = sum(1 for f in self.files if f.priority == "P1")
        lines.append("=" * 50)
        lines.append(f"📈 处理计划：")
        lines.append(f"   🔴 P0 深读精算: {p0_count} 个文件")
        lines.append(f"   🟡 P1 定向抓取: {p1_count} 个文件")
        lines.append(f"   ⚪ P2 扫读备查: {self.total_files - p0_count - p1_count} 个文件")

        # 未分类率：没有指标就没有优化目标
        other_count = len(by_module.get("other", []))
        rate = other_count / self.total_files * 100 if self.total_files else 0
        lines.append("")
        lines.append(f"❓ 未分类率: {other_count}/{self.total_files}（{rate:.1f}%）")
        if other_count:
            lines.append("   可在报告页对未分类文件指定分类，下次自动生效")

        # 扫描版 PDF：没有文字层，只能 OCR
        scanned = [f for f in self.files if f.is_scanned]
        if scanned:
            lines.append("")
            lines.append(f"📄 扫描版 PDF: {len(scanned)} 个（无文字层，按重要性 OCR）")
            for f in sorted(scanned, key=lambda x: x.importance, reverse=True)[:3]:
                lines.append(f"   • {f.path.name[:40]}（重要性 {f.importance}）")

        # 老版 .doc 转换情况
        doc_files = [f for f in self.files if f.path.suffix.lower() == ".doc"]
        if doc_files:
            converted = sum(1 for f in doc_files if f.converted_path)
            lines.append("")
            lines.append(f"📄 老版 .doc: {len(doc_files)} 个，已自动转换 {converted} 个")
            if converted < len(doc_files):
                lines.append("   ⚠️ 未转换的需要 LibreOffice 或 Word：装好后重跑，或手动另存为 .docx")

        return "\n".join(lines)
