"""执行 SQL 生成节点产出的查询语句。"""

from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.repositories.query_contracts import QueryExecutionResult
from app.agent.state import AgentState


async def execute_sql(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """执行 SQL 并把查询结果写回 AgentState。"""
    writer = runtime.stream_writer
    step = "执行 SQL"
    writer({"type": "progress", "step": step, "node": "execute_sql", "status": "running"})

    sql = state.get("sql", "").strip()
    if not sql:
        raise ValueError("没有可执行的 SQL。")

    if "query_max_rows" in state:
        query_result = await runtime.context["dw_repository"].execute_query(
            sql, max_rows=state["query_max_rows"]
        )
    else:
        # 普通 Agent 图沿用仓库默认上限；Harness query_data 会显式注入上限。
        query_result = await runtime.context["dw_repository"].execute_query(sql)
    if isinstance(query_result, QueryExecutionResult):
        rows = query_result.rows
        query_limitations = (
            [f"查询结果超过 {query_result.max_rows} 行，已截断。"]
            if query_result.truncated
            else []
        )
    else:
        # 保持旧仓库替身和已有 Agent 测试的最小接口兼容。
        rows = list(query_result)
        query_limitations = []
    writer(
        {
            "type": "execute_sql",
            "step": step,
            "node": "execute_sql",
            "status": "success",
            "row_count": len(rows),
            "rows": rows,
            "limitations": query_limitations,
        }
    )
    return {
        "sql_result": rows,
        "query_limitations": query_limitations,
    }
