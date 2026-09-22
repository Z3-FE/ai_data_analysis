"""LangGraph 状态定义。

旧图（问数/分析/日常聊天）的共享状态 schema：字段按管线阶段分组注释，
节点只读写自己阶段的字段；Harness 工具调用时身份五元组从 HarnessRunRef 冗余注入。
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from app.agent.state_result_store.state import HarnessControlState


class AgentState(TypedDict, total=False):
    """Agent 图共享的中间状态。"""

    # ── 身份与输入：Harness 工具调用时从 run_ref 冗余注入，事件与记忆治理都凭它 ──
    input_text: str              # 本次请求文本（分析模式下同 original_question）
    user_id: str
    conversation_id: str
    thread_id: str
    turn_id: str
    run_id: str
    original_question: str       # 用户原始问题，召回与 SQL 生成的基准
    asset_ids: list[str]         # 本轮登记附件资产
    # 多轮消息累积：add_messages reducer 按 id 追加/去重；统一由 finalize_turn 写入
    messages: Annotated[list[AnyMessage], add_messages]
    # ── 路由：route_question 产出，决定 daily_chat / single_query / analysis / clarification 去向 ──
    execution_mode: str
    route_reason: str
    analysis_goals: list[str]    # 判定为分析时给出的目标清单，plan_analysis 的输入
    route_confidence: float
    clarification_question: str
    route_output: str
    # ── 分析分支：plan_analysis → execute_analysis，任务级查询与计算结果 ──
    analysis_plan: dict
    analysis_task_results: list[dict]
    analysis_evidence: dict
    # ── 报告分支：generate_report_plan → render_report，单问数与分析两条链的公共终点 ──
    report_plan: dict
    report_plan_status: str
    report_plan_error: str
    rendered_report: dict
    # ── 关键词抽取（extract_keywords）：后续四路召回词的来源 ──
    llm_keywords: list[str]
    jieba_keywords: list[str]
    keywords: list[str]
    # ── 四路并行召回（retrieve_columns/tables/metrics/dimension_values）：每路 词→候选 ──
    column_recall_terms: list[str]
    column_candidates: list[dict]
    table_recall_terms: list[str]
    table_candidates: list[dict]
    metrics_recall_terms: list[str]
    metrics_candidates: list[dict]
    dimension_value_recall_terms: list[str]
    dimension_value_candidates: list[dict]
    # ── 上下文整合与过滤：merge_retrieved_info → filter_metric/filter_table → reconcile → add_extra_context ──
    table_infos: list[dict]
    metric_infos: list[dict]
    dimension_infos: list[dict]
    relationship_infos: list[dict]
    metric_dimension_infos: list[dict]
    metric_selection: list[str]
    table_selection: dict[str, list[str]]
    extra_context: dict
    # ── SQL 生成与执行（generate_sql → execute_sql） ──
    sql: str
    sql_reasoning: str
    # Harness 查询工具传给 Query Agent 的最大结果行数。
    query_max_rows: int
    sql_result: list[dict]
    query_limitations: list[str]
    result_columns: list[dict]
    # ── 维度值映射与展示（enrich_query_result） ──
    dimension_value_mappings: list[dict]
    display_sql_result: list[dict]
    mapping_limitations: list[str]
    # ── 输出：daily_chat / finalize_turn 的最终回复 ──
    output_text: str
    llm_output: str

    # ── Harness 控制字段：HarnessGraphState 运行时写入，普通图运行不涉及 ──
    harness: HarnessControlState


class HarnessGraphState(AgentState, total=False):
    """Harness 循环运行的状态：在 AgentState 之上仅补 project_id；harness 控制字段由父类持有。"""

    project_id: str | None  # 仅 Harness 请求（new/restore）携带的项目 ID


__all__ = ["AgentState", "HarnessGraphState"]
