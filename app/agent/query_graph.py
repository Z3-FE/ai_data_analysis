"""可复用的问数 Agent 图。

该子图复用现有问数流程，但刻意跳过问题路由：调用方已经完成路由，
只需要把一个具体的查询问题交给 Query Agent。分析任务通过 astream
消费它的过程事件和最终 SQL 结果，不改变问数节点自身的职责。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.context import AgentContext
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.enrich_query_result import enrich_query_result
from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.extract_keywords import extract_keywords_node
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.reconcile_filtered_context import reconcile_filtered_context
from app.agent.nodes.retrieve_columns import retrieve_columns
from app.agent.nodes.retrieve_dimension_values import retrieve_dimension_values
from app.agent.nodes.retrieve_metrics import retrieve_metrics
from app.agent.nodes.retrieve_tables import retrieve_tables
from app.agent.state import AgentState


def add_query_flow(graph: StateGraph, terminal_node: str | None = None) -> None:
    """向图中注册现有问数节点及其内部连接。

连接关系保持现有问数链路：关键词抽取后并行召回，
召回信息汇合后过滤上下文，最后生成并执行 SQL。
"""
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
    graph.add_node("enrich_query_result", enrich_query_result)
    # 关键词抽取后并行执行四路召回，减少相互独立的 I/O 等待。
    graph.add_edge("extract_keywords", "retrieve_columns")
    graph.add_edge("extract_keywords", "retrieve_tables")
    graph.add_edge("extract_keywords", "retrieve_metrics")
    graph.add_edge("extract_keywords", "retrieve_dimension_values")
    # 四路召回全部完成后统一合并，避免过滤节点只看到部分候选。
    graph.add_edge("retrieve_columns", "merge_retrieved_info")
    graph.add_edge("retrieve_tables", "merge_retrieved_info")
    graph.add_edge("retrieve_metrics", "merge_retrieved_info")
    graph.add_edge("retrieve_dimension_values", "merge_retrieved_info")
    # 指标和表字段过滤彼此独立，完成后由 reconcile 节点统一校验。
    graph.add_edge("merge_retrieved_info", "filter_metric")
    graph.add_edge("merge_retrieved_info", "filter_table")
    graph.add_edge("filter_metric", "reconcile_filtered_context")
    graph.add_edge("filter_table", "reconcile_filtered_context")
    # 过滤结果补全 JOIN 和指标上下文，再进入 SQL 生成。
    graph.add_edge("reconcile_filtered_context", "add_extra_context")
    graph.add_edge("add_extra_context", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    # SQL 执行后统一补齐结果字段语义和维度值展示映射。
    graph.add_edge("execute_sql", "enrich_query_result")
    if terminal_node:
        # 顶层图将查询结果交给最终报告节点；分析任务子图直接返回 State。
        graph.add_edge("enrich_query_result", terminal_node)
    else:
        # 分析任务需要继续读取增强后的 State 组装 TaskResult。
        graph.add_edge("enrich_query_result", END)


def build_query_graph():
    """构建跳过问题路由的问数 Agent 图。"""
    graph = StateGraph(state_schema=AgentState, context_schema=AgentContext)
    add_query_flow(graph)
    graph.add_edge(START, "extract_keywords")
    return graph.compile()


query_graph = build_query_graph()
