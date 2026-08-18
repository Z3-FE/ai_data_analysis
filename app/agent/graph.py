"""Agent 图编排。

当前链路已经落地关键词抽取和字段召回。外部依赖通过 AgentContext 注入，
节点只读写 AgentState 中的业务中间结果。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.context import AgentContext
from app.agent.nodes.extract_keywords import extract_keywords_node
from app.agent.nodes.retrieve_columns import retrieve_columns
from app.agent.nodes.retrieve_tables import retrieve_tables
from app.agent.nodes.retrieve_metrics import retrieve_metrics
from app.agent.nodes.retrieve_dimension_values import retrieve_dimension_values
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.reconcile_filtered_context import reconcile_filtered_context
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.execute_sql import execute_sql
from app.agent.state import AgentState


def build_agent_graph():
    """构建并返回当前阶段的 LangGraph。"""
    graph = StateGraph(state_schema=AgentState, context_schema=AgentContext)
    graph.add_node("extract_keywords", extract_keywords_node)
    graph.add_node("retrieve_columns", retrieve_columns)
    graph.add_node("retrieve_tables", retrieve_tables)
    graph.add_node("retrieve_metrics", retrieve_metrics)
    graph.add_node("retrieve_dimension_values", retrieve_dimension_values)
    graph.add_node("merge_retrieved_info", merge_retrieved_info)
    graph.add_node("filter_metric", filter_metric)
    graph.add_node("filter_table", filter_table)
    graph.add_node("reconcile_filtered_context", reconcile_filtered_context)
    graph.add_node("add_extra_context", add_extra_context)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("execute_sql", execute_sql)

    graph.add_edge(START, "extract_keywords")
    # 关键词抽取后并行执行表、字段、指标和维度值四路召回。
    graph.add_edge("extract_keywords", "retrieve_columns")
    graph.add_edge("extract_keywords", "retrieve_tables")
    graph.add_edge("extract_keywords", "retrieve_metrics")
    graph.add_edge("extract_keywords", "retrieve_dimension_values")

    # 四路召回全部完成后，再统一进入召回信息合并节点。
    graph.add_edge("retrieve_columns", "merge_retrieved_info")
    graph.add_edge("retrieve_tables", "merge_retrieved_info")
    graph.add_edge("retrieve_metrics", "merge_retrieved_info")
    graph.add_edge("retrieve_dimension_values", "merge_retrieved_info")
    # 指标和表字段过滤彼此独立，因此在合并完成后并行执行。
    # 两个节点只负责返回 LLM 的选择结果，最终由 reconcile 节点统一校验、裁剪和补全。
    graph.add_edge("merge_retrieved_info", "filter_metric")
    graph.add_edge("merge_retrieved_info", "filter_table")
    # 这里是汇合点：必须等指标过滤和表字段过滤都完成后再进行依赖补全。
    graph.add_edge("filter_metric", "reconcile_filtered_context")
    graph.add_edge("filter_table", "reconcile_filtered_context")
    graph.add_edge("reconcile_filtered_context", "add_extra_context")
    graph.add_edge("add_extra_context", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    graph.add_edge("execute_sql", END)
    return graph.compile()


agent_graph = build_agent_graph()
