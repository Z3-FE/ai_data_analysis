"""LangGraph 状态定义。"""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """最小 Agent 状态。"""

    input_text: str
    original_question: str
    llm_keywords: list[str]
    jieba_keywords: list[str]
    keywords: list[str]
    column_recall_terms: list[str]
    column_candidates: list[dict]
    output_text: str
    llm_output: str
