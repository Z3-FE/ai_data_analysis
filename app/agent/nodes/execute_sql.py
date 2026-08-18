"""执行 SQL 生成节点产出的查询语句。"""

from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState


async def execute_sql(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """执行 SQL 并把查询结果写回 AgentState。"""
    writer = runtime.stream_writer
    step = "执行 SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    sql = state.get("sql", "").strip()
    if not sql:
        raise ValueError("没有可执行的 SQL。")

    rows = await runtime.context["dw_repository"].execute_query(sql)
    writer(
        {
            "type": "execute_sql",
            "step": step,
            "status": "success",
            "row_count": len(rows),
            "rows": rows,
        }
    )
    return {"sql_result": rows}
