"""Agent 图编排。

当前链路已经落地关键词抽取和字段召回。外部依赖通过 AgentContext 注入，
节点只读写 AgentState 中的业务中间结果。

入口先经过问题路由：日常聊天进入文本回答节点，简单查询进入 Query Agent，
复杂问题进入分析计划，缺少关键信息则停在澄清边界。分析侧再通过
query_graph 复用现有问数链。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.context import AgentContext
from app.agent.nodes.daily_chat import daily_chat
from app.agent.nodes.execute_analysis import execute_analysis
from app.agent.nodes.generate_report_plan import generate_report_plan
from app.agent.nodes.plan_analysis import plan_analysis
from app.agent.nodes.render_report import render_report
from app.agent.nodes.route_question import route_question
from app.agent.query_graph import add_query_flow
from app.agent.state import AgentState


def _route_after_question(state: AgentState) -> str:
    """把路由节点的结构化结果映射到图分支。

这里是结构化字段到 LangGraph 条件边名称的唯一转换点。
"""
    return state.get("execution_mode", "single_query")


async def _clarification_route_boundary(
    state: AgentState,
    runtime,
) -> AgentState:
    """返回路由节点生成的最小澄清问题。

澄清分支不进入 Query Agent，避免在用户信息不足时生成无依据的 SQL。
"""
    message = state.get("clarification_question") or "请补充问题中的关键指标或范围。"
    runtime.stream_writer(
        {
            "type": "route_boundary",
            "step": "问题路由",
            "node": "clarification_route_boundary",
            "status": "success",
            "execution_mode": "clarification",
            "message": message,
        }
    )
    return {"output_text": message}


def build_agent_graph():
    """构建并返回当前阶段的 LangGraph。

路由节点是四个执行分支的边界；分析任务内部的依赖调度由
execute_analysis 负责，不在 LangGraph 中为每个动态任务创建节点。
"""
    graph = StateGraph(state_schema=AgentState, context_schema=AgentContext)
    # 先判断问题是否需要多步查询和确定性计算。
    graph.add_node("route_question", route_question)
    graph.add_node("daily_chat", daily_chat)
    graph.add_node("plan_analysis", plan_analysis)
    graph.add_node("execute_analysis", execute_analysis)
    graph.add_node("generate_report_plan", generate_report_plan)
    graph.add_node("render_report", render_report)
    graph.add_node("clarification_route_boundary", _clarification_route_boundary)
    # 普通问数和复杂分析都先生成报告规划，再绑定真实数据。
    add_query_flow(graph, terminal_node="generate_report_plan")

    graph.add_edge(START, "route_question")
    # 路由结果决定进入日常聊天、现有问数链、分析链或澄清边界。
    graph.add_conditional_edges(
        "route_question",
        _route_after_question,
        {
            "daily_chat": "daily_chat",
            "single_query": "extract_keywords",
            "analysis": "plan_analysis",
            "clarification": "clarification_route_boundary",
        },
    )
    graph.add_edge("plan_analysis", "execute_analysis")
    # 分析证据完成后由 LLM 生成规划，再由后端渲染最终报告。
    graph.add_edge("execute_analysis", "generate_report_plan")
    graph.add_edge("generate_report_plan", "render_report")
    graph.add_edge("render_report", END)
    graph.add_edge("clarification_route_boundary", END)
    graph.add_edge("daily_chat", END)
    return graph.compile()


agent_graph = build_agent_graph()
