"""关键词抽取节点。

这是 LangGraph 的第一块积木：只负责从用户问题中提取对后续检索有用的关键词。
当前采用三路策略：LLM 负责语义理解，jieba 负责词面补充，原始问题负责兜底。
"""

import json
import re

import jieba.analyse
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState

_JSON_ARRAY_PATTERN = re.compile(r"\[[\s\S]*\]")
_STOP_WORDS = {
    "帮我",
    "看一下",
    "统计一下",
    "统计",
    "一下",
    "看看",
    "帮忙",
    "数据",
    "多少",
    "一下吧",
}
_ALLOW_POS = (
    "n",  # 名词：商品、订单、销售额
    "nr",  # 人名：张三、李四
    "ns",  # 地名：华北、北京、上海
    "nt",  # 机构团体名：门店、品牌、渠道
    "nz",  # 其他专有名词：SKU、GMV、AOV
    "v",  # 动词：统计、对比、查询
    "vn",  # 名动词：销售、成交、退款
    "a",  # 形容词：新增、有效、活跃
    "an",  # 名形词：可用、有效、异常
    "eng",  # 英文：GMV、SKU、ROI
    "i",  # 成语或习用语，避免遗漏整体表达
    "l",  # 常用固定短语，例如“销售总额”
)


def _parse_keywords(raw_text: str) -> list[str]:
    """从模型输出中解析关键词列表。"""
    text = raw_text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_ARRAY_PATTERN.search(text)
        if not match:
            return []
        data = json.loads(match.group(0))

    if not isinstance(data, list):
        return []
    return _dedupe_keywords(str(item) for item in data)


def _extract_jieba_keywords(question: str) -> list[str]:
    """使用 jieba TF-IDF 提取词面关键词。"""
    words = jieba.analyse.extract_tags(
        question,
        topK=8,
        withWeight=False,
        allowPOS=_ALLOW_POS,
    )
    return _dedupe_keywords(words)


def _dedupe_keywords(values) -> list[str]:
    """清洗、过滤并保持顺序去重。"""
    keywords = []
    seen = set()
    for value in values:
        keyword = str(value).strip()
        if not keyword or keyword in _STOP_WORDS or len(keyword) <= 1:
            continue
        if keyword not in seen:
            seen.add(keyword)
            keywords.append(keyword)
    return keywords


def _merge_keywords(llm_keywords: list[str], jieba_keywords: list[str]) -> list[str]:
    """按 LLM 优先、jieba 补充的顺序合并关键词。"""
    return _dedupe_keywords([*llm_keywords, *jieba_keywords])


def extract_keywords_node(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> AgentState:
    """调用 LLM 和 jieba 从用户问题中抽取关键词。"""
    writer = runtime.stream_writer
    step = "抽取关键词"
    writer({"type": "progress", "step": step, "status": "running"})

    question = state.get("input_text", "")
    prompt = f"""
你是数据分析系统中的关键词抽取节点。
请从用户问题中提取最适合后续检索的关键词。

要求：
1. 只输出 JSON 数组，不要输出解释文字。
2. 保留指标词、维度词、时间词、过滤条件词。
3. 去掉“帮我”“看一下”“统计一下”这类无意义表达。
4. 如果没有明确关键词，返回空数组 []。

用户问题：{question}
""".strip()

    llm_output = runtime.context["llm_client"].chat(prompt)
    llm_keywords = _parse_keywords(llm_output)
    jieba_keywords = _extract_jieba_keywords(question)
    keywords = _merge_keywords(llm_keywords, jieba_keywords)

    writer(
        {
            "type": "keywords",
            "step": step,
            "status": "success",
            "keywords": keywords,
            "llm_keywords": llm_keywords,
            "jieba_keywords": jieba_keywords,
        }
    )
    return {
        "original_question": question,
        "llm_keywords": llm_keywords,
        "jieba_keywords": jieba_keywords,
        "keywords": keywords,
        "llm_output": llm_output,
        "output_text": json.dumps(
            {
                "keywords": keywords,
                "llm_keywords": llm_keywords,
                "jieba_keywords": jieba_keywords,
                "original_question": question,
            },
            ensure_ascii=False,
        ),
    }
