"""基于现有 query_graph 的真实数据查询工具。"""

from __future__ import annotations

from app.agent.context import AgentContext
from app.agent.query_graph import query_graph
from app.agent.state import AgentState
from app.agent.state_result_store.contracts import HarnessRunRef

from .contracts import QueryDataInput, QueryDataOutput


class QueryDataTool:
    """把 Harness 工具调用映射到现有 Query Graph。"""

    name = "query_data"

    def __init__(
        self,
        *,
        context: AgentContext,
        run_ref: HarnessRunRef,
        asset_ids: tuple[str, ...] = (),
    ) -> None:
        self.context = context
        self.run_ref = run_ref
        self.asset_ids = asset_ids

    async def execute(self, value: QueryDataInput) -> QueryDataOutput:
        state: AgentState = {
            "input_text": value.query,
            "original_question": value.query,
            "user_id": self.run_ref.user_id,
            "conversation_id": self.run_ref.conversation_id,
            "thread_id": self.run_ref.thread_id,
            "turn_id": self.run_ref.turn_id,
            "run_id": self.run_ref.run_id,
            "asset_ids": list(self.asset_ids),
        }
        result = await query_graph.ainvoke(state, context=self.context)
        rows = list(result.get("sql_result", []))
        display_rows = list(result.get("display_sql_result", rows))
        return QueryDataOutput(
            row_count=len(rows),
            column_count=len(result.get("result_columns", [])),
            sql=str(result.get("sql", "")),
            rows=rows,
            display_rows=display_rows,
            result_columns=list(result.get("result_columns", [])),
            mapping_limitations=list(result.get("mapping_limitations", [])),
        )


__all__ = ["QueryDataTool"]
