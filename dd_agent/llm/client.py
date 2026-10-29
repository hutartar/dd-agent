"""
LLM客户端：支持本地Ollama模型和云端API
计算和规则判断不经过LLM
没有API key时自动降级为规则输出
"""
import os
from typing import Optional

from ..runtime_config import get_llm_config


def _normalize_ollama_host(value: str) -> str:
    """把 OLLAMA_BASE_URL 规整成 http://host:port，去掉结尾的 / 或 /v1。"""
    host = (value or "http://localhost:11434").strip().rstrip("/")
    if host.endswith("/v1"):
        host = host[:-3]
    return host


class LLMClient:
    """
    LLM客户端（可选增强模块）
    用法:
        llm = LLMClient(provider="ollama", model="qwen2.5:7b")
        if llm.available:
            summary = llm.summarize_legal_text(text)
    """

    def __init__(self, provider: str = None, model: str = None,
                 api_key: str = None, temperature: float = 0.2,
                 base_url: str = None):
        # 优先级：显式参数 > 设置页保存的 settings.json > 环境变量 > 默认值
        runtime = get_llm_config() or {}
        self.provider = (provider or runtime.get("provider")
                         or os.getenv("DD_LLM_PROVIDER") or "ollama").strip().lower()
        self.temperature = temperature

        if self.provider == "ollama":
            self.ollama_host = _normalize_ollama_host(
                base_url or runtime.get("base_url")
                or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434"
            )
            self.base_url = self.ollama_host + "/v1"
            self.api_key = "ollama"  # Ollama不需要真实key
            self.model = (model or runtime.get("model")
                          or os.getenv("DD_LLM_MODEL") or "qwen2.5:7b")
        else:
            self.base_url = (
                base_url
                or runtime.get("base_url")
                or os.getenv("DD_LLM_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or "https://api.openai.com/v1"
            ).rstrip("/")
            self.api_key = (
                api_key
                or runtime.get("api_key")
                or os.getenv("DD_LLM_API_KEY")
                or os.getenv("OPENAI_API_KEY", "")
            )
            self.model = (model or runtime.get("model")
                          or os.getenv("DD_LLM_MODEL") or "gpt-4o-mini")

        self._client = None

        self._client = None

    @property
    def available(self) -> bool:
        """是否可用（本地Ollama或云端API）"""
        if self.provider == "ollama":
            # 检查Ollama是否在运行
            try:
                import requests
                r = requests.get(f"{self.ollama_host}/api/tags", timeout=2)
                return r.status_code == 200
            except:
                return False
        return bool(self.api_key)

    def summarize_legal_text(self, text: str) -> str:
        """
        摘要法律文件（扫描版OCR后用LLM提取关键条款）
        无API key时返回提示信息
        """
        if not self.available:
            return "（LLM未配置，需人工阅读法律文件提取关键条款）"

        prompt = f"""
        请从以下合同文本中提取关键条款，用简洁的条目输出：
        1. 年租金金额
        2. 租期（起止时间）
        3. 租金递增条款
        4. 押金金额
        5. 提前解约违约金条款
        6. 出租方名称

        合同文本：
        {text[:3000]}
        """
        return self._call_api(prompt)

    def extract_ota_terms(self, text: str) -> str:
        """从OTA协议文本中提取关键条款"""
        if not self.available:
            return "（LLM未配置，无法自动提取OTA协议条款）"

        prompt = f"""
        请从以下OTA渠道合作协议文本中提取关键条款，用简洁的条目输出：
        1. 渠道名称（携程/美团/抖音/其他）
        2. 佣金费率（百分比，比如15%）
        3. 结算周期/账期（比如月结、T+7）
        4. 合作范围（客房/餐饮/全品类）
        5. 是否独家合作
        6. 协议有效期

        协议文本：
        {text[:3000]}
        """
        return self._call_api(prompt)

    def extract_credit_summary(self, text: str) -> str:
        """从征信报告提取关键风险信息"""
        if not self.available:
            return "（LLM未配置，无法自动提取征信报告）"

        prompt = f"""
        请从以下征信报告文本中提取关键风险信息，用简洁的条目输出：
        1. 有没有逾期记录（信用卡/贷款）
        2. 有没有欠税记录
        3. 现有贷款余额是多少
        4. 有没有对外担保
        5. 有没有行政处罚或法院执行记录
        6. 整体信用评价（良好/一般/较差）

        征信报告文本：
        {text[:3000]}
        """
        return self._call_api(prompt)

    def extract_charter_terms(self, text: str) -> str:
        """从公司章程提取关键条款"""
        if not self.available:
            return "（LLM未配置，无法自动提取公司章程条款）"

        prompt = f"""
        请从以下公司章程文本中提取关键条款，用简洁的条目输出：
        1. 法定代表人是谁
        2. 注册资本和实缴期限
        3. 分红条款（怎么分红）
        4. 股权转让限制
        5. 有没有对赌/回购条款
        6. 决策机制（重大事项怎么决策）

        公司章程文本：
        {text[:3000]}
        """
        return self._call_api(prompt)

    def plan_charter_pages(self, intro_text: str) -> dict:
        """
        根据前2页的OCR文本，判断：
        1. 这是封面/目录？还是正文？
        2. 关键条款（分红、股权转让、组织机构）在第几页？
        返回 {"content_type": "目录/正文", "key_pages": [3,5,7]}
        """
        if not self.available:
            return {"content_type": "未知", "key_pages": []}

        prompt = f"""
        这是公司章程PDF前2页的OCR文本。请判断：

        1. 这2页是什么内容？（封面+目录 / 正文开头 / 其他）
        2. 如果是目录，"分红条款"、"股权转让"、"组织机构"这些关键章节分别在第几页？
        3. 如果已经是正文开头了，直接说"正文"

        请用以下格式回答（严格遵守，不要多余文字）：
        content_type: 目录
        key_pages: 3,5,7

        或者：
        content_type: 正文
        key_pages:

        前2页文本：
        {intro_text[:2000]}
        """
        result = self._call_api(prompt)

        # 解析返回结果
        plan = {"content_type": "未知", "key_pages": []}
        for line in result.split("\n"):
            line = line.strip()
            if line.startswith("content_type:"):
                plan["content_type"] = line.split(":", 1)[1].strip()
            elif line.startswith("key_pages:"):
                pages_str = line.split(":", 1)[1].strip()
                if pages_str:
                    try:
                        plan["key_pages"] = [int(p.strip()) for p in pages_str.split(",") if p.strip().isdigit()]
                    except:
                        pass
        return plan

    def explain_anomaly(self, metric: str, value: float, expected: float) -> str:
        """用LLM解释财务异常（可选功能）"""
        if not self.available:
            return f"{metric}={value}, 预期{expected}（LLM未配置，无法自动解释原因）"

        prompt = f"""
        这是酒店行业财务尽调中发现的一个异常：
        指标：{metric}
        实际值：{value}
        预期范围：{expected}

        请给出3个可能的原因，按可能性排序，每个原因一句话。
        """
        return self._call_api(prompt)

    def polish_report(self, raw_text: str) -> str:
        """润色报告文字（可选）"""
        if not self.available:
            return raw_text

        prompt = f"""
        请润色以下尽调报告的文字，保持数据不变，让结论更专业、更有判断力。
        不要改变任何数字和表格，只优化文字表述：

        {raw_text[:5000]}
        """
        return self._call_api(prompt)

    def generate(self, prompt: str, max_tokens: int = None) -> str:
        """
        公开的通用调用入口。

        给调用方一个稳定接口，不用碰私有 _call_api；失败时返回带"（LLM调用失败"
        的字符串（和 _call_api 一致），调用方据此判断是否回退。
        """
        return self._call_api(prompt)

    def _call_api(self, prompt: str) -> str:
        """调用LLM接口（Ollama本地或OpenAI兼容）"""
        try:
            if self.provider == "ollama":
                # 直接调用Ollama原生API
                import requests
                url = f"{self.ollama_host}/api/chat"
                data = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                }
                response = requests.post(url, json=data, timeout=120)
                result = response.json()
                return result["message"]["content"]
            else:
                # OpenAI 兼容接口（DeepSeek / 硅基流动 / 通义等），不依赖 openai 包
                import requests

                if "platform.deepseek.com" in self.base_url:
                    return (
                        "（LLM调用失败：接口地址填的是 platform.deepseek.com，"
                        "那是 DeepSeek 网页控制台，不是 API。请改成 https://api.deepseek.com/v1）"
                    )

                url = self.base_url.rstrip("/") + "/chat/completions"
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.temperature,
                }
                response = requests.post(url, json=payload, headers=headers, timeout=120)

                if response.status_code == 401:
                    return "（LLM调用失败：API Key 无效或没有权限，检查 Key 是否复制完整）"
                if response.status_code == 404:
                    return (
                        f"（LLM调用失败：接口地址不对（HTTP 404）。当前 base_url 是 {self.base_url}，"
                        "DeepSeek 应为 https://api.deepseek.com/v1）"
                    )
                if response.status_code in (402, 429):
                    return (
                        "（LLM调用失败：余额不足或请求太频繁（HTTP 429）。"
                        "先去 DeepSeek 控制台看余额和额度，或等几分钟再试）"
                    )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"（LLM调用失败：{e}）"
