"""
OCR提取器：从扫描版PDF提取文本
支持多种后端：pytesseract / paddleocr / easyocr
检测到哪个可用就用哪个，都没有就降级
"""
# 先导入numpy兼容性补丁，解决旧版scipy问题
from .numpy_compat import *

from pathlib import Path
from typing import Optional


class OCRExtractor:
    """
    OCR文本提取器
    用法:
        ocr = OCRExtractor()
        if ocr.available:
            text = ocr.extract_pdf("合同.pdf")
    """

    # 单例：PaddleOCR只初始化一次，不要每个PDF都重新初始化（省大量时间）
    _paddleocr_instance = None

    def __init__(self):
        self.backend = self._detect_backend()
        # OCR缓存目录
        self.cache_dir = Path("web/results/.ocr_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_paddleocr(self):
        """单例模式：只初始化一次PaddleOCR"""
        if OCRExtractor._paddleocr_instance is None:
            from paddleocr import PaddleOCR
            OCRExtractor._paddleocr_instance = PaddleOCR(
                use_angle_cls=True,
                lang="ch",
                det_limit_side_len=640,
            )
        return OCRExtractor._paddleocr_instance

    def _detect_backend(self) -> str:
        """检测可用的OCR后端"""
        # 试试paddleocr
        try:
            import paddleocr
            return "paddleocr"
        except ImportError:
            pass

        # 试试easyocr
        try:
            import easyocr
            return "easyocr"
        except ImportError:
            pass

        # 试试pytesseract
        try:
            import pytesseract
            return "pytesseract"
        except ImportError:
            pass

        return "none"

    @property
    def available(self) -> bool:
        """OCR是否可用"""
        return self.backend != "none"

    def extract_pdf(self, pdf_path: str | Path, max_pages: int = 10) -> str:
        """
        从扫描版PDF提取文本（从第1页开始）
        """
        return self.extract_pdf_pages(pdf_path, start_page=0, end_page=max_pages-1)

    def extract_pdf_pages(self, pdf_path: str | Path, start_page: int = 0, end_page: int = 9) -> str:
        """
        从PDF提取指定页码范围的文本（0-indexed）
        优先直接读文字版PDF，只有扫描版才走OCR
        带简单缓存（测试用）
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            return ""

        # === 查OCR缓存：用文件内容MD5做key，不看路径，换目录也命中缓存 ===
        import hashlib
        # 读文件内容算MD5，不管路径在哪，内容一样就命中
        file_content = pdf_path.read_bytes()
        file_md5 = hashlib.md5(file_content).hexdigest()
        cache_key = f"{file_md5}_{start_page}_{end_page}"
        cache_file = self.cache_dir / f"{cache_key}.txt"
        if cache_file.exists():
            return cache_file.read_text(encoding="utf-8")

        # === 第一步：先尝试直接读文字版PDF，跳过OCR（省大量时间）===
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(str(pdf_path)) as pdf:
                n_pages = len(pdf.pages)
                start = max(0, start_page)
                end = min(n_pages - 1, end_page)
                for i in range(start, end + 1):
                    page_text = pdf.pages[i].extract_text() or ""
                    text_parts.append(page_text)
            direct_text = "\n".join(text_parts).strip()
            # 如果直接读出来的文字超过50个字符，说明是文字版PDF，直接返回，不走OCR
            if len(direct_text) > 50:
                cache_file.write_text(direct_text, encoding="utf-8")  # 存缓存
                return direct_text
        except Exception:
            pass  # 读失败就继续走OCR

        # === 第二步：确实是扫描版PDF，才走OCR ===
        if not self.available:
            return ""

        result = ""
        if self.backend == "paddleocr":
            result = self._extract_paddleocr(pdf_path, start_page, end_page)
        elif self.backend == "easyocr":
            result = self._extract_easyocr(pdf_path, start_page, end_page)
        elif self.backend == "pytesseract":
            result = self._extract_tesseract(pdf_path, start_page, end_page)

        # 存缓存
        if result:
            cache_file.write_text(result, encoding="utf-8")
        return result

    def _extract_paddleocr(self, pdf_path: Path, start_page: int = 0, end_page: int = 9) -> str:
        """用PaddleOCR提取（优化版：降分辨率+单例初始化）"""
        try:
            import pypdfium2 as pdfium
            import numpy as np

            # 用单例的PaddleOCR实例，不重复初始化
            ocr = self._get_paddleocr()

            # 用pypdfium2把PDF转成图片
            pdf = pdfium.PdfDocument(str(pdf_path))
            n_pages = len(pdf)
            # 限制页码范围
            start = max(0, start_page)
            end = min(n_pages - 1, end_page)

            all_text = []
            for i in range(start, end + 1):
                page = pdf[i]
                # dpi=150，速度优先
                bitmap = page.render(scale=150/72)
                pil_image = bitmap.to_pil()
                # 转成numpy数组
                img_array = np.array(pil_image)

                result = ocr.ocr(img_array, cls=True)

                # 从结果中提取文字
                if result and result[0]:
                    for line in result[0]:
                        all_text.append(line[1][0])

            pdf.close()
            return "\n".join(all_text)
        except Exception as e:
            print(f"PaddleOCR提取失败: {e}")
            import traceback
            traceback.print_exc()
            return ""

    def _extract_easyocr(self, pdf_path: Path, max_pages: int) -> str:
        """用EasyOCR提取"""
        try:
            import pdf2image
            import easyocr

            reader = easyocr.Reader(["ch_sim", "en"], gpu=False)
            images = pdf2image.convert_from_path(str(pdf_path), dpi=200, first_page=1, last_page=max_pages)

            all_text = []
            for img in images:
                result = reader.readtext(img)
                for line in result:
                    all_text.append(line[1])
            return "\n".join(all_text)
        except Exception as e:
            print(f"EasyOCR提取失败: {e}")
            return ""

    def _extract_tesseract(self, pdf_path: Path, max_pages: int) -> str:
        """用Tesseract提取"""
        try:
            import pdf2image
            import pytesseract

            images = pdf2image.convert_from_path(str(pdf_path), dpi=200, first_page=1, last_page=max_pages)

            all_text = []
            for img in images:
                text = pytesseract.image_to_string(img, lang="chi_sim")
                all_text.append(text)
            return "\n".join(all_text)
        except Exception as e:
            print(f"Tesseract提取失败: {e}")
            return ""
