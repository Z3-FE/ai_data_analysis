"""LangGraph 状态定义。"""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """Agent 图共享的中间状态。

字段按生命周期分组：路由和分析字段由分析入口写入，
问数字段由可复用 Query Agent 写入，最后由服务层整理成接口响应。
"""

    input_text: str
    session_id: str
    original_question: str

    # 问题路由和分析计划字段。
    execution_mode: str
    route_reason: str
    analysis_goals: list[str]
    route_confidence: float
    clarification_question: str
    route_output: str
    # LLM 生成的复杂分析任务和依赖计划。
    analysis_plan: dict
    # 全部任务的完整 TaskResult，服务依赖执行、审计和展示数据绑定。
    analysis_task_results: list[dict]
    # 从完整任务结果提取的精简可信证据，服务结论总结器。
    analysis_evidence: dict
    # 最终报告节点生成并绑定真实数据后的唯一前端报告。
    final_report: dict

    # Query Agent 的关键词、召回候选和结构化 SQL 上下文。
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

    # Query Agent 的最终 SQL、查询结果和普通问数输出。
    sql: str
    sql_reasoning: str
    sql_result: list[dict]
    result_columns: list[dict]
    dimension_value_mappings: list[dict]
    display_sql_result: list[dict]
    mapping_limitations: list[str]
    output_text: str
    llm_output: str
