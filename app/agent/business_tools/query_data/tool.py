"""基于现有 query_graph 的真实数据查询工具。"""

from __future__ import annotations

from app.agent.streaming.writer import HarnessEventWriter, NullHarnessEventWriter
from app.agent.context import AgentContext
from app.agent.query_graph import query_graph
from app.agent.state import AgentState
from app.agent.state_result_store.contracts import HarnessRunRef

from .contracts import QueryDataInput, QueryDataOutput


class QueryDataTool:
    """把 Harness 工具调用映射到现有 Query Graph。"""

    name = "query_data"
    input_model = QueryDataInput

    def __init__(
        self,
        *,
        context: AgentContext,
        run_ref: HarnessRunRef,
        asset_ids: tuple[str, ...] = (),
        max_rows: int = 2_000,
        event_writer: HarnessEventWriter | None = None,
    ) -> None:
        if max_rows <= 0:
            raise ValueError("max_rows 必须大于 0")
        self.context = context
        self.run_ref = run_ref
        self.asset_ids = asset_ids
        self.max_rows = max_rows
        self.event_writer = event_writer or NullHarnessEventWriter(run_ref=run_ref)

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
            "query_max_rows": self.max_rows,
        }
        latest_state: dict | None = None
        with self.event_writer.bind(
            source=self.name,
            phase="execute_tool",
            action_id=None,
        ):
            async for event in query_graph.astream(
                state,
                context=self.context,
                stream_mode=["custom", "values"],
            ):
                if not isinstance(event, tuple) or len(event) != 2:
                    continue
                mode, payload = event
                if mode == "custom":
                    if isinstance(payload, dict):
                        self.event_writer.custom(payload)
                elif mode == "values" and isinstance(payload, dict):
                    latest_state = payload

        if latest_state is None:
            raise RuntimeError("query_graph 未返回最终 values 状态")
        result = latest_state
        rows = list(result.get("sql_result", []))
        display_rows = list(result.get("display_sql_result", rows))
        query_limitations = list(result.get("query_limitations", []))
        truncated = bool(query_limitations)
        result_columns = list(result.get("result_columns", []))
        preview_rows = display_rows[:20]
        summary = (
            f"query_data 查询完成，返回 {len(rows)} 行、{len(result_columns)} 个字段。"
        )
        if query_limitations:
            summary += " 限制：" + "；".join(dict.fromkeys(query_limitations))
        if preview_rows:
            summary += f" 前 {len(preview_rows)} 行预览：{preview_rows!r}"
        return QueryDataOutput(
            row_count=len(rows),
            column_count=len(result_columns),
            summary=summary,
            truncated=truncated,
            max_rows=self.max_rows,
            preview_rows=preview_rows,
            sql=str(result.get("sql", "")),
            rows=rows,
            display_rows=display_rows,
            result_columns=result_columns,
            limitations=query_limitations,
            mapping_limitations=list(result.get("mapping_limitations", [])),
        )


__all__ = ["QueryDataTool"]
