"""Agent 服务层。

负责创建初始 State、组装 AgentContext、执行 LangGraph，并把结果包装成接口层可用
的数据结构。路由层不直接接触 LangGraph 细节。
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from uuid import uuid4

from app.agent.context import AgentContext
from app.agent.graph import agent_graph as default_agent_graph
from app.agent.memory.contracts import TurnMemoryInput
from app.agent.memory.formation_service import MemoryFormationService
from app.agent.state import AgentState
from app.agent.turn_output import build_turn_output
from app.core.config import settings
from app.repositories.conversation_repository import ConversationRepository
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

logger = logging.getLogger(__name__)
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
        conversation_repository: ConversationRepository,
        graph: Any | None = None,
        memory_formation_service: MemoryFormationService | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.embedding_client = embedding_client
        self.dimension_value_search = dimension_value_search
        self.meta_tables_semantic_repository = meta_tables_semantic_repository
        self.meta_columns_semantic_repository = meta_columns_semantic_repository
        self.meta_metrics_semantic_repository = meta_metrics_semantic_repository
        self.meta_dimension_values_semantic_repository = (
            meta_dimension_values_semantic_repository
        )
        self.meta_catalog_repository = meta_catalog_repository
        self.dw_repository = dw_repository
        self.conversation_repository = conversation_repository
        self.agent_graph = graph or default_agent_graph
        # M3 只在本轮历史成功落库后运行，不参与下一轮上下文拼接。
        self.memory_formation_service = memory_formation_service

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
            metadata_recall_semaphore=asyncio.Semaphore(
                settings.metadata_recall.max_concurrent_terms
            ),
        )

    def _new_identity(self, conversation_id: str) -> dict[str, str]:
        """为一次 Agent 执行生成轮次身份，并固定线程与会话的映射。"""
        return {
            "user_id": settings.app.default_user_id,
            "conversation_id": conversation_id,
            "thread_id": conversation_id,
            "turn_id": str(uuid4()),
            "run_id": str(uuid4()),
        }

    @staticmethod
    def _new_turn_state() -> AgentState:
        """显式清空本轮临时字段，避免 Checkpointer 复用上一轮的中间结果。"""
        return {
            "original_question": "",
            "execution_mode": "",
            "route_reason": "",
            "analysis_goals": [],
            "route_confidence": 0.0,
            "clarification_question": "",
            "route_output": "",
            "analysis_plan": {},
            "analysis_task_results": [],
            "analysis_evidence": {},
            "report_plan": {},
            "report_plan_status": "",
            "report_plan_error": "",
            "rendered_report": {},
            "llm_keywords": [],
            "jieba_keywords": [],
            "keywords": [],
            "column_recall_terms": [],
            "column_candidates": [],
            "table_recall_terms": [],
            "table_candidates": [],
            "metrics_recall_terms": [],
            "metrics_candidates": [],
            "dimension_value_recall_terms": [],
            "dimension_value_candidates": [],
            "table_infos": [],
            "metric_infos": [],
            "dimension_infos": [],
            "relationship_infos": [],
            "metric_dimension_infos": [],
            "metric_selection": [],
            "table_selection": {},
            "extra_context": {},
            "sql": "",
            "sql_reasoning": "",
            "sql_result": [],
            "result_columns": [],
            "dimension_value_mappings": [],
            "display_sql_result": [],
            "mapping_limitations": [],
            "output_text": "",
            "llm_output": "",
        }

    @staticmethod
    def _with_identity(
        event: dict[str, Any], identity: dict[str, str]
    ) -> dict[str, Any]:
        """给所有业务 SSE 事件补充本轮身份，节点不需要重复拼接。"""
        return {**event, **identity}

    def _format_result(self, input_text: str, result: AgentState) -> dict:
        """把图执行结果整理成接口响应结构。"""
        return {
            "input_text": input_text,
            "user_id": result.get("user_id", settings.app.default_user_id),
            "conversation_id": result.get("conversation_id", ""),
            "thread_id": result.get("thread_id", result.get("conversation_id", "")),
            "turn_id": result.get("turn_id", ""),
            "run_id": result.get("run_id", ""),
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
            "report_plan": result.get("report_plan", {}),
            "report_plan_status": result.get("report_plan_status", ""),
            "report_plan_error": result.get("report_plan_error", ""),
            "rendered_report": result.get("rendered_report", {}),
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
            "dimension_value_candidates": result.get("dimension_value_candidates", []),
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
            "dimension_value_mappings": result.get("dimension_value_mappings", []),
            "display_sql_result": result.get("display_sql_result", []),
            "mapping_limitations": result.get("mapping_limitations", []),
            "output_text": result.get("output_text", ""),
            "llm_output": result.get("llm_output", ""),
        }

    @staticmethod
    def _result_status(result: AgentState) -> str:
        """把图结果映射成应用历史使用的轮次状态。"""
        report = result.get("rendered_report")
        if isinstance(report, dict) and report:
            report_status = report.get("status")
            if report_status == "failed":
                return "failed"
            if report_status == "partial":
                return "partial"
            return "completed"
        if result.get("execution_mode") == "clarification":
            return "completed"
        return "completed" if result.get("output_text") else "failed"

    @classmethod
    def _message_status(cls, result: AgentState) -> str:
        """把内部轮次状态转换成消息完成事件使用的状态。"""
        status = cls._result_status(result)
        return "success" if status == "completed" else status

    @staticmethod
    def _history_output(result: AgentState) -> tuple[str, dict[str, Any], str]:
        """提取历史需要的助手文本和可重渲染输出。

        这里刻意只保存最终报告、澄清消息或受限的查询结果，不把 Agent State
        中的 SQL、Python、完整执行事件和完整 rows 写入聊天历史。
        """
        return build_turn_output(result)

    async def _save_turn_start(self, input_text: str, identity: dict[str, str]) -> bool:
        """保存轮次开始和用户消息；历史写入失败不阻断 Agent 执行。"""
        try:
            saved = await self.conversation_repository.start_turn(
                conversation_id=identity["conversation_id"],
                user_id=identity["user_id"],
                thread_id=identity["thread_id"],
                turn_id=identity["turn_id"],
                run_id=identity["run_id"],
                input_text=input_text,
            )
            return saved is not False
        except Exception:
            logger.exception("会话轮次开始保存失败：turn_id=%s", identity["turn_id"])
            return False

    async def _save_turn_finish(
        self,
        identity: dict[str, str],
        result: AgentState,
        *,
        status: str | None = None,
        error_message: str = "",
        execution_trace: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any], str, bool]:
        """保存助手最终消息和受控输出，并返回同一份前端事件内容。"""
        output_type, output_payload, assistant_content = self._history_output(result)
        final_status = status or self._result_status(result)
        try:
            saved = await self.conversation_repository.finish_turn(
                conversation_id=identity["conversation_id"],
                user_id=identity["user_id"],
                turn_id=identity["turn_id"],
                execution_mode=result.get("execution_mode", "single_query"),
                status=final_status,
                assistant_content=assistant_content,
                output_type=output_type,
                output_payload=output_payload,
                execution_trace=execution_trace,
                error_message=error_message,
            )
            history_saved = saved is not False
        except Exception:
            logger.exception("会话轮次完成保存失败：turn_id=%s", identity["turn_id"])
            history_saved = False
        return output_type, output_payload, assistant_content, history_saved

    async def _submit_memory_formation(
        self,
        identity: dict[str, str],
        result: AgentState,
        *,
        input_text: str,
        asset_ids: list[str],
        history_saved: bool,
        status: str | None = None,
    ) -> None:
        """在历史落库成功后提交 M3；形成失败不能影响主执行结果。"""
        if not history_saved or self.memory_formation_service is None:
            return
        output_type, output_payload, assistant_content = self._history_output(result)
        try:
            await self.memory_formation_service.submit(
                TurnMemoryInput(
                    user_id=identity["user_id"],
                    conversation_id=identity["conversation_id"],
                    turn_id=identity["turn_id"],
                    run_id=identity["run_id"],
                    # 使用服务层收到的原始问题，避免图的最终状态被节点裁剪后丢失。
                    input_text=input_text,
                    assistant_content=assistant_content,
                    execution_mode=str(result.get("execution_mode") or ""),
                    status=status or self._result_status(result),
                    output_type=output_type,
                    output_payload=output_payload,
                    asset_ids=asset_ids,
                )
            )
        except Exception:
            # 记忆是回答之后的增强能力，不能覆盖已经生成的回答或原始错误。
            logger.exception(
                "长期记忆形成提交失败：conversation_id=%s turn_id=%s",
                identity["conversation_id"],
                identity["turn_id"],
            )

    def run(
        self,
        input_text: str,
        conversation_id: str,
        *,
        asset_ids: list[str] | None = None,
    ) -> dict:
        """同步执行当前 Agent 图并返回结构化结果。"""

        async def execute_and_drain() -> dict:
            try:
                return await self._run_async(
                    input_text,
                    conversation_id,
                    asset_ids=asset_ids,
                )
            finally:
                # 同步入口的临时事件循环即将关闭，必须先排空自动形成任务。
                if self.memory_formation_service is not None:
                    await self.memory_formation_service.close()

        return asyncio.run(execute_and_drain())

    async def arun(
        self,
        input_text: str,
        conversation_id: str,
        *,
        asset_ids: list[str] | None = None,
    ) -> dict:
        """在当前异步事件循环内执行 Agent 图。"""
        return await self._run_async(
            input_text,
            conversation_id,
            asset_ids=asset_ids,
        )

    async def _run_async(
        self,
        input_text: str,
        conversation_id: str,
        *,
        asset_ids: list[str] | None = None,
    ) -> dict:
        """在同一个事件循环内完成同步接口使用的完整轮次生命周期。"""
        identity = self._new_identity(conversation_id)
        request_asset_ids = list(dict.fromkeys(asset_ids or []))
        state: AgentState = AgentState(
            input_text=input_text,
            asset_ids=request_asset_ids,
            **identity,
            **self._new_turn_state(),
        )
        history_started = await self._save_turn_start(input_text, identity)
        config = self._graph_config(identity)
        try:
            result = await self.agent_graph.ainvoke(
                input=state, config=config, context=self._context()
            )
        except Exception as exc:
            failed_state: AgentState = {**state, "report_plan_error": str(exc)}
            _, _, _, history_saved = await self._save_turn_finish(
                identity,
                failed_state,
                status="failed",
                error_message=str(exc),
            )
            await self._submit_memory_formation(
                identity,
                failed_state,
                input_text=input_text,
                asset_ids=request_asset_ids,
                history_saved=history_started and history_saved,
                status="failed",
            )
            raise
        result = {**identity, **result}
        _, _, _, history_saved = await self._save_turn_finish(identity, result)
        await self._submit_memory_formation(
            identity,
            result,
            input_text=input_text,
            asset_ids=request_asset_ids,
            history_saved=history_started and history_saved,
        )
        return self._format_result(input_text, result)

    @staticmethod
    def _graph_config(identity: dict[str, str]) -> dict[str, Any]:
        """为每次图执行构造官方 Checkpointer 所需的 thread 配置。"""
        return {
            "configurable": {
                "thread_id": identity["thread_id"],
            },
            "metadata": {
                "conversation_id": identity["conversation_id"],
                "turn_id": identity["turn_id"],
                "run_id": identity["run_id"],
            },
        }

    async def qyStream(
        self,
        input_text: str,
        conversation_id: str,
        *,
        asset_ids: list[str] | None = None,
    ) -> AsyncIterator[str]:
        """以带心跳的 SSE 文本流返回当前 Agent 执行过程。"""
        identity = self._new_identity(conversation_id)
        request_asset_ids = list(dict.fromkeys(asset_ids or []))
        state: AgentState = AgentState(
            input_text=input_text,
            asset_ids=request_asset_ids,
            **identity,
            **self._new_turn_state(),
        )
        final_state: AgentState = state
        trace_events: list[dict[str, Any]] = []
        history_started = False

        def trace_event(event: dict[str, Any]) -> dict[str, Any]:
            """给实时事件补身份，并保留一份用于历史回放的事件。"""
            enriched = self._with_identity(event, identity)
            trace_events.append(enriched)
            return enriched

        def execution_trace() -> dict[str, Any]:
            """生成历史回放载荷；思考和正文 chunk 合并，避免数据库保存上千条碎片。"""
            compacted: list[dict[str, Any]] = []
            stream_indexes: dict[str, int] = {}
            stream_types = {"reasoning_chunk", "llm_chunk"}
            limited_array_fields = {
                "rows",
                "data",
                "display_sql_result",
                "preview_rows",
            }

            def limit_trace_value(value: Any, field: str = "") -> Any:
                if isinstance(value, list):
                    items = value[:1000] if field in limited_array_fields else value
                    return [limit_trace_value(item) for item in items]
                if isinstance(value, dict):
                    return {
                        key: limit_trace_value(item, str(key))
                        for key, item in value.items()
                    }
                return value

            for sequence, event in enumerate(trace_events):
                event_type = event.get("type")
                if event_type not in stream_types:
                    if event_type == "message.completed":
                        content = event.get("content")
                        compacted.append(
                            {
                                key: value
                                for key, value in event.items()
                                if key not in {"content", "output"}
                            }
                            | {
                                "content_chars": len(content)
                                if isinstance(content, str)
                                else 0
                            }
                        )
                    else:
                        compacted.append(event)
                    continue

                phase = event.get("phase", "")
                scope = "::".join(
                    [
                        str(event_type),
                        str(event.get("node", "")),
                        str(event.get("task_id", "")),
                        str(phase),
                    ]
                )
                chunk = event.get("chunk")
                chunk_text = chunk if isinstance(chunk, str) else ""
                existing_index = stream_indexes.get(scope)
                if existing_index is None:
                    compacted.append(
                        {
                            **event,
                            "chunk": chunk_text,
                            "combined_text": chunk_text,
                            "chunk_count": 1,
                            "first_sequence": sequence,
                            "last_sequence": sequence,
                        }
                    )
                    stream_indexes[scope] = len(compacted) - 1
                    continue

                current = compacted[existing_index]
                combined_text = str(current.get("combined_text", "")) + chunk_text
                compacted[existing_index] = {
                    **current,
                    "chunk": combined_text,
                    "combined_text": combined_text,
                    "chunk_count": int(current.get("chunk_count", 1)) + 1,
                    "last_sequence": sequence,
                }

            return {
                "turn_id": identity["turn_id"],
                "run_id": identity["run_id"],
                "event_count": len(trace_events),
                "stored_event_count": len(compacted),
                "events": limit_trace_value(compacted),
            }

        async def identity_events() -> AsyncIterator[dict[str, Any]]:
            """先发送运行身份，再转发当前图产生的原始业务事件。"""
            nonlocal final_state, history_started
            try:
                config = self._graph_config(identity)
                history_started = await self._save_turn_start(input_text, identity)
            except asyncio.CancelledError:
                raise

            yield trace_event(
                {
                    "type": "run.started",
                    "step": "开始执行",
                    "node": "agent_service",
                    "status": "running",
                }
            )
            try:
                async for stream_item in self.agent_graph.astream(
                    input=state,
                    config=config,
                    context=self._context(),
                    stream_mode=["custom", "values"],
                ):
                    if isinstance(stream_item, tuple) and len(stream_item) == 2:
                        stream_mode, payload = stream_item
                        if stream_mode == "custom" and isinstance(payload, dict):
                            yield trace_event(payload)
                        elif stream_mode == "values" and isinstance(payload, dict):
                            final_state = {**final_state, **payload}
                    elif isinstance(stream_item, dict):
                        if "type" in stream_item:
                            yield trace_event(stream_item)
                        else:
                            final_state = {**final_state, **stream_item}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failed_state: AgentState = {
                    **final_state,
                    "report_plan_error": str(exc),
                }
                failed_event = trace_event(
                    {
                        "type": "run.failed",
                        "step": "执行失败",
                        "node": "agent_service",
                        "status": "failed",
                        "message": str(exc),
                    }
                )
                _, _, _, history_saved = await self._save_turn_finish(
                    identity,
                    failed_state,
                    status="failed",
                    error_message=str(exc),
                    execution_trace=execution_trace(),
                )
                await self._submit_memory_formation(
                    identity,
                    failed_state,
                    input_text=input_text,
                    asset_ids=request_asset_ids,
                    history_saved=history_started and history_saved,
                    status="failed",
                )
                yield failed_event
                return

            output_type, output_payload, assistant_content = self._history_output(
                final_state
            )
            completion_message = None
            if assistant_content:
                completion_message = trace_event(
                    {
                        "type": "message.completed",
                        "step": "生成回答",
                        "node": "agent_service",
                        "status": self._message_status(final_state),
                        "execution_mode": final_state.get(
                            "execution_mode", "single_query"
                        ),
                        "content": assistant_content,
                        "output_type": output_type,
                        "output": output_payload,
                    }
                )
            completion_event = trace_event(
                {
                    "type": "run.completed",
                    "step": "执行完成",
                    "node": "agent_service",
                    "status": self._result_status(final_state),
                    "execution_mode": final_state.get("execution_mode", "single_query"),
                }
            )
            _, _, _, history_saved = await self._save_turn_finish(
                identity,
                final_state,
                execution_trace=execution_trace(),
            )
            await self._submit_memory_formation(
                identity,
                final_state,
                input_text=input_text,
                asset_ids=request_asset_ids,
                history_saved=history_started and history_saved,
            )
            if completion_message:
                yield completion_message

            yield completion_event

        async for payload in _stream_sse_with_heartbeat(identity_events()):
            yield payload
