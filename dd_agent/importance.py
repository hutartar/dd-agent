"""
用 LLM 判断文件对尽调的重要性（0-100）。

关键词只能看文件名，LLM 能看懂"这份说明里提到了兜底承诺"这类上下文。
只在 LLM 可用时调用，失败就回退关键词评分。
"""

PROMPT = """你是财务尽调助手。判断下面这份资料对本次尽调的重要性，给 0-100 分。

文件：{name}
目录：{folder}
内容摘录：
{text}

打分标准：
- 80-100：直接决定风险判断（财务报表、租赁/投资合同、担保或承诺函、诉讼处罚、还款计划）
- 50-79：重要佐证（银行流水、纳税申报、证照、股权结构、审计报告）
- 20-49：背景资料（商业计划、项目介绍、宣传材料）
- 0-19：无关、重复或纯图片无信息

只输出两行，不要其他内容：
分数: <0-100 的整数>
理由: <20 字以内>"""


def _parse(raw: str):
    score = None
    reason = ""
    for line in (raw or "").splitlines():
        line = line.strip()
        if line.startswith("分数"):
            digits = "".join(ch for ch in line.split(":", 1)[-1] if ch.isdigit())
            if digits:
                score = int(digits[:3])
        elif line.startswith("理由"):
            reason = line.split(":", 1)[-1].strip()[:40]
    if score is None:
        return None
    return {"score": max(0, min(100, score)), "reason": reason}


def judge(llm, name: str, folder: str, text: str, max_chars: int = 800):
    """返回 {'score': int, 'reason': str} 或 None。"""
    if not llm or not getattr(llm, "available", False):
        return None
    prompt = PROMPT.format(name=name, folder=folder, text=(text or "")[:max_chars])
    try:
        raw = llm.generate(prompt)
    except Exception:
        return None
    return _parse(raw)


# ---------------------------------------------------------------- 分类 + 打分（一次调用）

CLASSIFY_PROMPT = """你是财务尽调助手。给下面这份文件分类，并判断它的重要性。

文件：{name}
目录：{folder}
内容摘录：
{text}

分类只能从下面六个里选一个：
财务、法律、资质、管理层、业务、其他

输出三行，不要其他内容：
分类: <六个之一>
分数: <0-100 的整数>
理由: <20 字以内>"""

MODULE_MAP = {
    "财务": ("financial", "财务资料"),
    "法律": ("legal", "法律文件"),
    "资质": ("qualification", "资质文件"),
    "管理层": ("management", "管理层资料"),
    "业务": ("business", "业务资料"),
    "其他": ("other", "其他"),
}


def classify_and_judge(llm, name: str, folder: str, text: str, max_chars: int = 800):
    """一次 LLM 调用同时给分类和重要性，返回 {module, category, score, reason}。"""
    if not llm or not getattr(llm, "available", False):
        return None

    prompt = CLASSIFY_PROMPT.format(name=name, folder=folder, text=(text or "")[:max_chars])
    try:
        raw = llm.generate(prompt)
    except Exception:
        return None

    kind = ""
    score = None
    reason = ""
    for line in (raw or "").splitlines():
        line = line.strip()
        if line.startswith("分类"):
            kind = line.split(":", 1)[-1].strip()
        elif line.startswith("分数"):
            digits = "".join(ch for ch in line.split(":", 1)[-1] if ch.isdigit())
            if digits:
                score = int(digits[:3])
        elif line.startswith("理由"):
            reason = line.split(":", 1)[-1].strip()[:40]

    module, category = MODULE_MAP.get(kind, ("other", "其他"))
    return {
        "module": module,
        "category": category,
        "score": max(0, min(100, score)) if score is not None else None,
        "reason": reason,
    }
