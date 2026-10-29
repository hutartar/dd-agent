"""
多格式数据提取器
支持: Excel(xls/xlsx)、PDF、Word(docx)
"""
import pandas as pd
from pathlib import Path
from typing import Dict, List, Any
import warnings
warnings.filterwarnings("ignore")


class DataExtractor:
    """从分类后的文件中提取结构化财务数据"""

    def __init__(self, classified_files: Dict[str, List]):
        self.files = classified_files
        self.data: Dict[str, Any] = {}

    def extract_all(self) -> Dict[str, Any]:
        """提取所有模块数据"""
        self.data["income_statements"] = self._extract_excel_tables("利润表")
        self.data["balance_sheets"] = self._extract_excel_tables("资产负债表")
        self.data["bank_flows"] = self._extract_bank_flows()
        self.data["ops_data"] = self._extract_ops_data()
        self.data["tax_declarations"] = self._extract_tax_data()
        return self.data

    def _extract_excel_tables(self, keyword: str) -> List[pd.DataFrame]:
        """从文件名含关键词的Excel文件中提取所有sheet"""
        results = []
        for module_files in self.files.values():
            for f in module_files:
                if f.file_type != "table":
                    continue
                if keyword in f.path.name:
                    try:
                        xl = pd.ExcelFile(f.path)
                        for sheet in xl.sheet_names:
                            df = pd.read_excel(f.path, sheet_name=sheet, header=None)
                            df.attrs["source_file"] = f.path.name
                            df.attrs["sheet"] = sheet
                            results.append(df)
                    except Exception as e:
                        print(f"WARN: 读取 {f.path.name} 失败: {e}")
        return results

    def _extract_bank_flows(self) -> pd.DataFrame:
        """提取银行流水"""
        for module_files in self.files.values():
            for f in module_files:
                if "流水" in f.path.name and f.file_type == "table":
                    try:
                        return pd.read_excel(f.path)
                    except Exception as e:
                        print(f"WARN: 读取银行流水失败: {e}")
        return pd.DataFrame()

    def _extract_ops_data(self) -> pd.DataFrame:
        """提取运营数据（入住率/PMS）"""
        for module_files in self.files.values():
            for f in module_files:
                if any(k in f.path.name for k in ["入住率", "客流", "pms", "后台系统"]):
                    if f.file_type == "table":
                        try:
                            return pd.read_excel(f.path)
                        except:
                            pass
        return pd.DataFrame()

    def _extract_tax_data(self) -> Dict:
        """提取纳税申报数据（PDF，需pdfplumber）"""
        # TODO: 实现pdfplumber提取
        return {}
