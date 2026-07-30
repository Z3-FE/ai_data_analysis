"""Agent 图编排。

当前链路已经落地关键词抽取和字段召回。外部依赖通过 AgentContext 注入，
节点只读写 AgentState 中的业务中间结果。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.context import AgentContext
from app.agent.nodes.extract_keywords import extract_keywords_node
from app.agent.nodes.retrieve_columns import retrieve_columns
from app.agent.state import AgentState


def build_agent_graph():
    """构建并返回当前阶段的 LangGraph。"""
    graph = StateGraph(state_schema=AgentState, context_schema=AgentContext)
    graph.add_node("extract_keywords", extract_keywords_node)
    graph.add_node("retrieve_columns", retrieve_columns)

    # graph.add_edge(START, "extract_keywords")
    # graph.add_edge("extract_keywords", END)
    #

    graph.add_edge(START, "extract_keywords")
    graph.add_edge("extract_keywords", "retrieve_columns")
    graph.add_edge("retrieve_columns", END)
    return graph.compile()


agent_graph = build_agent_graph()
