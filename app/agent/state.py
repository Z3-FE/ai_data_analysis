"""LangGraph 状态定义。"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.agent.state_result_store.state import HarnessControlState


class AgentState(TypedDict, total=False):
    """Agent 图共享的中间状态。"""

    input_text: str
    user_id: str
    conversation_id: str
    thread_id: str
    turn_id: str
    run_id: str
    original_question: str
    asset_ids: list[str]
    messages: Annotated[list[AnyMessage], add_messages]
    execution_mode: str
    route_reason: str
    analysis_goals: list[str]
    route_confidence: float
    clarification_question: str
    route_output: str
    analysis_plan: dict
    analysis_task_results: list[dict]
    analysis_evidence: dict
    report_plan: dict
    report_plan_status: str
    report_plan_error: str
    rendered_report: dict
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
    dimension_infos: list[dict]
    relationship_infos: list[dict]
    metric_dimension_infos: list[dict]
    metric_selection: list[str]
    table_selection: dict[str, list[str]]
    extra_context: dict
    sql: str
    sql_reasoning: str
    sql_result: list[dict]
    result_columns: list[dict]
    dimension_value_mappings: list[dict]
    display_sql_result: list[dict]
    mapping_limitations: list[str]
    output_text: str
    llm_output: str
    harness: HarnessControlState


class HarnessGraphState(AgentState, total=False):
    """现有 AgentState 加 Harness 控制字段的组合状态。"""

    project_id: str | None
    harness: HarnessControlState


__all__ = ["AgentState", "HarnessGraphState"]
