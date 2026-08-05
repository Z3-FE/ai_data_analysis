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
    table_recall_terms: list[str]
    table_candidates: list[dict]
    metrics_recall_terms: list[str]
    metrics_candidates: list[dict]
    dimension_value_recall_terms: list[str]
    dimension_value_candidates: list[dict]
    table_infos: list[dict]
    metric_infos: list[dict]
    relationship_infos: list[dict]
    metric_dimension_infos: list[dict]
    output_text: str
    llm_output: str
