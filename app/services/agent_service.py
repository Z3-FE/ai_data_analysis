"""Agent 服务层。

负责创建初始 State、组装 AgentContext、执行 LangGraph，并把结果包装成接口层可用
的数据结构。路由层不直接接触 LangGraph 细节。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from app.agent.context import AgentContext
from app.agent.graph import agent_graph
from app.agent.state import AgentState
from app.core.config import settings
from app.repositories.dw_repository import DwRepository
from app.repositories.es.es_dimension_value_repository import DimensionValueSearch
from app.repositories.mysql.meta.mysql_meta_catalog_repository import (
    MetaCatalogRepository,
)
from app.repositories.qdrant.qa_meta_columns_repository import (
    MetaColumnsSemanticRepository,
)
from app.repositories.qdrant.qa_meta_dimension_values_repository import (
    MetaDimensionValuesSemanticRepository,
)
from app.repositories.qdrant.qa_meta_metrics_repository import (
    MetaMetricsSemanticRepository,
)
from app.repositories.qdrant.qa_meta_tables_repository import (
    MetaTablesSemanticRepository,
)

SSE_HEARTBEAT_SECONDS = 15.0


async def _stream_sse_with_heartbeat(
    events: AsyncIterator[dict[str, Any]],
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """转为 SSE，并在业务流空闲时发送不会进入前端事件协议的注释心跳。"""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in events:
                queue.put_nowait(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            queue.put_nowait(
                {
                    "type": "error",
                    "step": "Agent 服务错误",
                    "node": "agent_service",
                    "message": str(exc),
                }
            )
        finally:
            queue.put_nowait(None)

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(),
                    timeout=heartbeat_seconds,
                )
            except TimeoutError:
                # SSE 注释行会经过代理维持连接，但不会被浏览器解析为业务事件。
                yield ": heartbeat\n\n"
                continue
            if event is None:
                return
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
    finally:
        if not producer.done():
            producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer


class AgentService:
    """封装当前问数 Agent 执行入口。"""

    def __init__(
        self,
        llm_client: Any,
        embedding_client: Any,
        dimension_value_search: DimensionValueSearch,
        meta_tables_semantic_repository: MetaTablesSemanticRepository,
        meta_columns_semantic_repository: MetaColumnsSemanticRepository,
        meta_metrics_semantic_repository: MetaMetricsSemanticRepository,
        meta_dimension_values_semantic_repository: MetaDimensionValuesSemanticRepository,
        meta_catalog_repository: MetaCatalogRepository,
        dw_repository: DwRepository,
    ) -> None:
        self.llm_client = llm_client
        self.embedding_client = embedding_client
        self.dimension_value_search = dimension_value_search
        self.meta_tables_semantic_repository = meta_tables_semantic_repository
        self.meta_columns_semantic_repository = meta_columns_semantic_repository
        self.meta_metrics_semantic_repository = meta_metrics_semantic_repository
        self.meta_dimension_values_semantic_repository = meta_dimension_values_semantic_repository
        self.meta_catalog_repository = meta_catalog_repository
        self.dw_repository = dw_repository

    def _context(self) -> AgentContext:
        """组装本次图执行使用的外部依赖。"""
        return AgentContext(
            llm_client=self.llm_client,
            embedding_client=self.embedding_client,
            dimension_value_search=self.dimension_value_search,
            meta_tables_semantic_repository=self.meta_tables_semantic_repository,
            meta_columns_semantic_repository=self.meta_columns_semantic_repository,
            meta_metrics_semantic_repository=self.meta_metrics_semantic_repository,
            meta_dimension_values_semantic_repository=self.meta_dimension_values_semantic_repository,
            meta_catalog_repository=self.meta_catalog_repository,
            dw_repository=self.dw_repository,
            llm_timeout_seconds=settings.llm.timeout_seconds,
        )

    def _format_result(self, input_text: str, result: AgentState) -> dict:
        """把图执行结果整理成接口响应结构。"""
        return {
            "input_text": input_text,
            "session_id": result.get("session_id", ""),
            "original_question": result.get("original_question", input_text),
            "execution_mode": result.get("execution_mode", "single_query"),
            "route_reason": result.get("route_reason", ""),
            "analysis_goals": result.get("analysis_goals", []),
            "route_confidence": result.get("route_confidence", 0.0),
            "clarification_question": result.get("clarification_question", ""),
            "route_output": result.get("route_output", ""),
            "analysis_plan": result.get("analysis_plan", {}),
            "analysis_task_results": result.get("analysis_task_results", []),
            "analysis_evidence": result.get("analysis_evidence", {}),
            "final_report": result.get("final_report", {}),
            "llm_keywords": result.get("llm_keywords", []),
            "jieba_keywords": result.get("jieba_keywords", []),
            "keywords": result.get("keywords", []),
            "column_recall_terms": result.get("column_recall_terms", []),
            "column_candidates": result.get("column_candidates", []),
            "table_recall_terms": result.get("table_recall_terms", []),
            "table_candidates": result.get("table_candidates", []),
            "metrics_recall_terms": result.get("metrics_recall_terms", []),
            "metrics_candidates": result.get("metrics_candidates", []),
            "dimension_value_recall_terms": result.get(
                "dimension_value_recall_terms", []
            ),
            "dimension_value_candidates": result.get(
                "dimension_value_candidates", []
            ),
            "table_infos": result.get("table_infos", []),
            "metric_infos": result.get("metric_infos", []),
            "dimension_infos": result.get("dimension_infos", []),
            "relationship_infos": result.get("relationship_infos", []),
            "metric_dimension_infos": result.get("metric_dimension_infos", []),
            "extra_context": result.get("extra_context", {}),
            "sql": result.get("sql", ""),
            "sql_reasoning": result.get("sql_reasoning", ""),
            "sql_result": result.get("sql_result", []),
            "result_columns": result.get("result_columns", []),
            "dimension_value_mappings": result.get(
                "dimension_value_mappings", []
            ),
            "display_sql_result": result.get("display_sql_result", []),
            "mapping_limitations": result.get("mapping_limitations", []),
            "output_text": result.get("output_text", ""),
            "llm_output": result.get("llm_output", ""),
        }

    def run(self, input_text: str, session_id: str = "") -> dict:
        """同步执行当前 Agent 图并返回结构化结果。"""
        state: AgentState = AgentState(input_text=input_text, session_id=session_id)
        result = asyncio.run(agent_graph.ainvoke(input=state, context=self._context()))
        return self._format_result(input_text, result)

    async def qyStream(
        self,
        input_text: str,
        session_id: str = "",
    ) -> AsyncIterator[str]:
        """以带心跳的 SSE 文本流返回当前 Agent 执行过程。"""
        state: AgentState = AgentState(input_text=input_text, session_id=session_id)
        events = agent_graph.astream(
            input=state,
            context=self._context(),
            stream_mode="custom",
        )
        async for payload in _stream_sse_with_heartbeat(events):
            yield payload
