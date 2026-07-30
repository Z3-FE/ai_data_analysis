"""关键词抽取节点。

这是 LangGraph 的第一块积木：只负责从用户问题中提取对后续检索有用的关键词。
当前采用三路策略：LLM 负责语义理解，jieba 负责词面补充，原始问题负责兜底。
"""

import json
import logging
import re

import jieba.analyse
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState

logger = logging.getLogger(__name__)

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
    "n",
    "nr",
    "ns",
    "nt",
    "nz",
    "v",
    "vn",
    "a",
    "an",
    "eng",
    "i",
    "l",
)

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


async def extract_keywords_node(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> AgentState:
    """调用 LLM 和 jieba 从用户问题中抽取关键词。"""
    writer = runtime.stream_writer
    step = "抽取关键词"
    writer({"type": "progress", "step": step, "status": "running"})

    question = state.get("input_text", "")
    prompt = PromptTemplate(
        template = load_prompt('extract_keywords'),
        input_variables=["query"]
    )
    chain = prompt | runtime.context["llm_client"] | JsonOutputParser()
    llm_keywords = await chain.ainvoke({"query": question})

    jieba_keywords = jieba.analyse.extract_tags(question, allowPOS=_ALLOW_POS)

    keywords = _merge_keywords(llm_keywords, jieba_keywords)


    logger.info("关键词抽取 LLM 原始输出：%s", llm_keywords)

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


