"""Harness 的本地 HTTP 运行入口。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.agent.business_tools.analyze_data import AnalyzeDataTool
from app.agent.business_tools.build_report import BuildReportTool
from app.agent.business_tools.query_data import QueryDataTool
from app.agent.context import AgentContext
from app.agent.context_engine.factory import build_context_engine
from app.agent.context_engine.harness_context import HarnessContextRequestFactory
from app.agent.finalization.errors import FinalizationFailure
from app.agent.finalization.service import PostgresFinalizationService
from app.agent.loop_controller.action_commit import (
    ActionCommitter,
)
from app.agent.loop_controller.contracts import (
    LoopResult,
    ResumeRunCommand,
    StartRunCommand,
)
from app.agent.loop_controller.controller import LoopController
from app.agent.planning_agent.agent import AutoLLMPlannerClient, PlanningAgent
from app.agent.state import HarnessGraphState
from app.agent.state_result_store.contracts import (
    ConfirmationReply,
    ErrorCategory,
    HarnessRunRef,
    HarnessStatus,
    HarnessStateSnapshot,
    LoopPhase,
    PlannerCapabilities,
    RunError,
    ToolSpec,
)
from app.agent.state_result_store.state import transition_harness_state
from app.agent.streaming.writer import (
    HarnessEventWriter,
    QueueEventSink,
    format_sse,
    stream_done_marker,
)
from app.agent.tool_runtime.registry import ToolRegistry
from app.agent.tool_runtime.runtime import ToolRuntime
from app.clients.elasticsearch_client import elasticsearch_client_manager
from app.clients.embedding_client import embedding_client_manager
from app.clients.llm_client import llm_client_manager
from app.clients.memory_client import memory_client_manager
from app.clients.mysql_client import dw_mysql_client_manager, meta_mysql_client_manager
from app.clients.postgres_client import postgres_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.core.config import settings
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.dw_repository import DwRepository
from app.repositories.es.es_dimension_value_repository import DimensionValueSearch
from app.repositories.finalization_repository import PostgresFinalizationLedger
from app.repositories.harness_action_repository import PostgresActionCommitter
from app.repositories.harness_artifact_repository import PostgresResultArtifactStore
from app.repositories.harness_run_repository import (
    HarnessPersistenceError,
    PostgresHarnessRunStore,
)
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

router = APIRouter(prefix="/harness", tags=["harness"])
logger = logging.getLogger(__name__)


class HarnessRunRequest(BaseModel):
    """创建 Harness 运行的请求；用户认证完成前暂时携带 user_id。"""

    input_text: str = Field(min_length=1, max_length=20_000)
    # 当前前端尚未接入登录；后续由认证依赖替换，而不是修改 Harness 状态模型。
    user_id: str = Field(
        default=settings.app.default_user_id,
        min_length=1,
        max_length=128,
    )
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: list[str] = Field(default_factory=list, max_length=32)


class HarnessResumeRequest(BaseModel):
    """提交用户确认回复；运行身份全部从服务端的 run 记录恢复。"""

    run_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(default=settings.app.default_user_id, min_length=1, max_length=128)
    confirmation_id: str = Field(min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=4_000)
    decision: Literal["confirm", "reject"] = "confirm"
    resolved_conditions: dict[str, Any] = Field(default_factory=dict)


class HarnessReconcileRequest(BaseModel):
    """触发一次收口对账；收口内容和运行现场全部来自服务端账本。"""

    run_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(default=settings.app.default_user_id, min_length=1, max_length=128)


def _require_runtime() -> tuple[Any, Any, Any]:
    """读取应用生命周期初始化的 Memory Runtime、PostgreSQL 和 LLM。"""
    runtime = memory_client_manager.runtime
    session_factory = postgres_client_manager.session_factory
    llm_client = llm_client_manager.client
    if runtime is None:
        raise RuntimeError("Memory Runtime 尚未初始化，请通过应用生命周期启动服务")
    if session_factory is None:
        raise RuntimeError("PostgreSQL Session 工厂尚未初始化，请通过应用生命周期启动服务")
    if llm_client is None:
        raise RuntimeError("LLM 客户端尚未初始化，请通过应用生命周期启动服务")
    return runtime, session_factory, llm_client


async def _ensure_conversation(*, conversation_id: str, user_id: str) -> dict[str, Any]:
    _, session_factory, _ = _require_runtime()
    return await ConversationRepository(
        session_factory=session_factory
    ).ensure_conversation(conversation_id=conversation_id, user_id=user_id)


def _agent_context(*, meta_session: Any, dw_session: Any, llm_client: Any) -> AgentContext:
    """在请求作用域内组装现有问数图所需的依赖。"""
    qdrant = qdrant_client_manager.client
    elasticsearch = elasticsearch_client_manager.client
    if qdrant is None or elasticsearch is None:
        raise RuntimeError("Meta 检索客户端尚未初始化")
    return AgentContext(
        llm_client=llm_client,
        embedding_client=embedding_client_manager.client,
        dimension_value_search=DimensionValueSearch(client=elasticsearch),
        meta_tables_semantic_repository=MetaTablesSemanticRepository(client=qdrant),
        meta_columns_semantic_repository=MetaColumnsSemanticRepository(client=qdrant),
        meta_metrics_semantic_repository=MetaMetricsSemanticRepository(client=qdrant),
        meta_dimension_values_semantic_repository=MetaDimensionValuesSemanticRepository(client=qdrant),
        meta_catalog_repository=MetaCatalogRepository(session=meta_session),
        dw_repository=DwRepository(session=dw_session),
        llm_timeout_seconds=settings.llm.timeout_seconds,
        metadata_recall_semaphore=asyncio.Semaphore(
            settings.metadata_recall.max_concurrent_terms
        ),
    )


def _query_tool_spec() -> ToolSpec:
    """声明 Harness 当前可用的问数工具及其结果持久化策略。"""
    return ToolSpec(
        name="query_data",
        description="使用现有问数链路查询数据，并返回受控摘要和结果引用。",
        permission="data.query.read",
        result_kind="artifact",
        artifact_kind="query_result",
        retry_on_timeout=False,
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string", "minLength": 1}},
            "additionalProperties": False,
        },
        timeout_seconds=settings.harness.query_timeout_seconds,
    )


def _analyze_tool_spec() -> ToolSpec:
    """声明 Harness 当前可用的分析工具及其 Artifact 策略。"""
    return ToolSpec(
        name="analyze_data",
        description="根据用户目标生成分析计划，查询真实数据并执行受限分析计算。",
        permission="data.analysis.read",
        result_kind="artifact",
        artifact_kind="analysis_result",
        idempotency="conditionally_idempotent",
        retry_on_timeout=False,
        input_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "analysis_goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 16,
                },
            },
            "additionalProperties": False,
        },
        timeout_seconds=settings.harness.analyze_timeout_seconds,
    )


def _report_tool_spec() -> ToolSpec:
    """声明 Harness 当前可用的报告工具及其报告 Artifact 策略。"""
    return ToolSpec(
        name="build_report",
        description=(
            "把已完成的 query_data 或 analyze_data 结果渲染为最终可视化报告。"
            "必须先执行数据工具，再把它们的 result_ref 传入 result_refs。"
        ),
        permission="data.report.write",
        result_kind="report",
        artifact_kind="rendered_report",
        idempotency="conditionally_idempotent",
        retry_on_timeout=False,
        input_schema={
            "type": "object",
            "required": ["goal", "result_refs"],
            "properties": {
                "goal": {"type": "string", "minLength": 1},
                "result_refs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": 8,
                },
            },
            "additionalProperties": False,
        },
        timeout_seconds=settings.harness.report_timeout_seconds,
    )


def _build_controller(
    *,
    run_ref: HarnessRunRef,
    asset_ids: tuple[str, ...],
    runtime: Any,
    session_factory: Any,
    llm_client: Any,
    meta_session: Any,
    dw_session: Any,
    event_writer: HarnessEventWriter | None = None,
) -> LoopController:
    """在请求作用域内组装完整 Harness；所有持久化实现均由此注入。"""
    query_spec = _query_tool_spec()
    analyze_spec = _analyze_tool_spec()
    report_spec = _report_tool_spec()
    artifact_store = PostgresResultArtifactStore(session_factory)
    agent_context = _agent_context(
        meta_session=meta_session,
        dw_session=dw_session,
        llm_client=llm_client,
    )
    query_tool = QueryDataTool(
        context=agent_context,
        run_ref=run_ref,
        asset_ids=asset_ids,
        event_writer=event_writer,
    )
    analyze_tool = AnalyzeDataTool(
        context=agent_context,
        run_ref=run_ref,
        asset_ids=asset_ids,
        event_writer=event_writer,
    )
    report_tool = BuildReportTool(
        context=agent_context,
        run_ref=run_ref,
        artifact_store=artifact_store,
        event_writer=event_writer,
    )
    tool_runtime = ToolRuntime(
        ToolRegistry(
            {
                "query_data": (query_spec, query_tool),
                "analyze_data": (analyze_spec, analyze_tool),
                "build_report": (report_spec, report_tool),
            }
        ),
        artifact_store=artifact_store,
        event_writer=event_writer,
    )
    context_engine = build_context_engine(
        memory_reader=runtime.manager,
        session_factory=session_factory,
        llm_client=llm_client,
        model_name=settings.llm.model_name,
    )
    run_store = PostgresHarnessRunStore(session_factory)
    finalization = PostgresFinalizationService(
        conversation_repository=ConversationRepository(session_factory),
        ledger=PostgresFinalizationLedger(session_factory),
        run_store=run_store,
        memory_formation_service=runtime.formation_service,
        artifact_store=artifact_store,
    )
    return LoopController(
        context_builder=context_engine,
        planning_agent=PlanningAgent(
            llm_client=AutoLLMPlannerClient(llm_client),
            capabilities=PlannerCapabilities(allow_ask_user=True),
        ),
        finalization_service=finalization,
        run_store=run_store,
        context_request_factory=HarnessContextRequestFactory(),
        action_committer=PostgresActionCommitter(session_factory),
        tool_runtime=tool_runtime,
        # 没有即时确认分派器；ASK_USER 必须持久化为 waiting_confirmation。
        confirmation_dispatcher=None,
        tool_specs=(query_spec, analyze_spec, report_spec),
        max_planner_retries=settings.harness.max_planner_retries,
        max_tool_retries=settings.harness.max_tool_retries,
        max_iterations=settings.harness.max_iterations,
        run_timeout_seconds=settings.harness.run_timeout_seconds,
        event_writer=event_writer,
    )


def _run_ref_from_state(state: dict[str, Any]) -> HarnessRunRef:
    """从数据库保存的运行现场重建完整身份，不接受客户端覆盖。"""
    try:
        return HarnessRunRef.model_validate(
            {
                field: state[field]
                for field in (
                    "user_id",
                    "conversation_id",
                    "thread_id",
                    "turn_id",
                    "run_id",
                )
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HarnessPersistenceError("Harness 运行现场缺少完整身份") from exc


def _serialize_result(result: LoopResult) -> dict[str, Any]:
    """只返回受控 LoopResult，不暴露完整 Prompt、隐藏思考或完整工具结果。"""
    return result.model_dump(mode="json")


def _status_payload(state: dict[str, Any]) -> dict[str, Any]:
    """生成运行查询响应；完整现场仍只留在 PostgreSQL。"""
    run_ref = _run_ref_from_state(state)
    snapshot = HarnessStateSnapshot.model_validate(state["harness"])
    return {
        "run_ref": run_ref.model_dump(mode="json"),
        "status": snapshot.status.value,
        "phase": snapshot.phase.value,
        "iteration": snapshot.iteration,
        "action_seq": snapshot.action_seq,
        "state_version": snapshot.state_version,
        "pending_confirmation": (
            snapshot.pending_confirmation.model_dump(mode="json")
            if snapshot.pending_confirmation is not None
            else None
        ),
        "last_error": (
            snapshot.last_error.model_dump(mode="json")
            if snapshot.last_error is not None
            else None
        ),
    }


def _http_error(exc: Exception) -> HTTPException:
    """把持久化身份、并发和状态冲突转换为明确的 HTTP 响应。"""
    if isinstance(exc, FinalizationFailure):
        # 收口中断不是终态失败；运行保持 running/finalization，由 reconcile 恢复。
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, HarnessPersistenceError):
        message = str(exc)
        status_code = 404 if "不存在" in message else 409
        return HTTPException(status_code=status_code, detail=message)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="Harness 运行失败")


async def _finish_unhandled_start_failure(
    *,
    session_factory: Any,
    run_ref: HarnessRunRef,
) -> None:
    """收口控制器启动前后未被自身捕获的异常，释放会话占用。"""
    should_finish_turn = True
    try:
        run_store = PostgresHarnessRunStore(session_factory)
        state = await run_store.load(run_ref)
        current_status = HarnessStatus(state["harness"]["status"])
        terminal_statuses = {
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
            HarnessStatus.TIMEOUT,
        }
        if current_status is HarnessStatus.WAITING_CONFIRMATION:
            # 恢复请求可能只是 confirmation_id 或条件字段校验失败，
            # 此时必须保留 waiting 状态，允许用户重新提交正确回复。
            should_finish_turn = False
        elif current_status in terminal_statuses:
            should_finish_turn = False
        elif (
            await PostgresFinalizationLedger(session_factory).get(
                run_id=run_ref.run_id, user_id=run_ref.user_id
            )
            is not None
        ):
            # 收口账本已锁定结果归属；运行现场保持 running/finalization，
            # 由 reconcile 恢复，这里伪造失败终态会覆盖锁定的收口内容。
            should_finish_turn = False
        else:
            state["harness"]["last_error"] = RunError(
                category=ErrorCategory.DATABASE,
                code="stream_operation_failed",
                message="Harness 流式执行失败，已收口为失败状态。",
                retryable=False,
            ).model_dump(mode="json")
            state["harness"] = transition_harness_state(
                state["harness"],
                status=HarnessStatus.RUNNING,
                phase=LoopPhase.FINALIZATION,
                terminal_intent=HarnessStatus.FAILED.value,
            )
            state["harness"] = transition_harness_state(
                state["harness"],
                status=HarnessStatus.FAILED,
                phase=LoopPhase.FINALIZATION,
            )
            await run_store.save(run_ref, state)
    except HarnessPersistenceError:
        # 控制器可能在创建 Harness 运行现场前失败；仍尝试结束已创建的 turn。
        pass
    except Exception:
        logger.exception(
            "Harness run failure cleanup failed: run_id=%s",
            run_ref.run_id,
        )
    if not should_finish_turn:
        return
    try:
        await ConversationRepository(session_factory).finish_turn(
            conversation_id=run_ref.conversation_id,
            user_id=run_ref.user_id,
            turn_id=run_ref.turn_id,
            execution_mode="harness",
            status="failed",
            assistant_content="任务启动失败，已停止后续处理。",
            output_type="text",
            output_payload={"message": "任务启动失败，已停止后续处理。"},
            error_message="Harness 控制器启动失败。",
        )
    except Exception:
        logger.exception(
            "Harness startup failure conversation cleanup failed: run_id=%s",
            run_ref.run_id,
        )


def _sse_response(
    operation: Callable[[], Awaitable[LoopResult]],
    *,
    run_ref: HarnessRunRef,
    queue: asyncio.Queue[Any],
    writer: HarnessEventWriter,
    heartbeat_seconds: float | None = None,
    on_operation_failure: Callable[[], Awaitable[None]] | None = None,
) -> StreamingResponse:
    """运行 Harness 并把统一事件队列转换为带心跳的 SSE。"""
    heartbeat_interval = (
        settings.harness.sse_heartbeat_seconds
        if heartbeat_seconds is None
        else heartbeat_seconds
    )

    async def run_operation() -> None:
        try:
            result = await operation()
            writer.emit(
                "run.result",
                source="harness",
                phase=(
                    result.phase.value
                    if hasattr(result.phase, "value")
                    else str(result.phase)
                ),
                iteration=int(getattr(result, "iteration", 0)),
                payload={"loop_result": _serialize_result(result)},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Harness stream operation failed: run_id=%s",
                run_ref.run_id,
            )
            if on_operation_failure is not None:
                try:
                    await on_operation_failure()
                except Exception:
                    logger.exception(
                        "Harness stream failure callback failed: run_id=%s",
                        run_ref.run_id,
                    )
            writer.emit(
                "stream.failed",
                source="harness",
                phase="finalization",
                payload={
                    "error_code": "stream_operation_failed",
                    "message": "Harness 流式执行失败，请通过状态接口查询最终状态。",
                },
            )
        finally:
            queue.put_nowait(stream_done_marker())

    task = asyncio.create_task(run_operation())

    async def body() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=heartbeat_interval,
                    )
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if item is stream_done_marker():
                    break
                yield format_sse(item)
        except asyncio.CancelledError:
            # 客户端断连时取消执行任务；LoopController 会把运行收口为 cancelled。
            if not task.done():
                task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                task.add_done_callback(_consume_task_result)
            raise
        finally:
            if not task.done():
                task.cancel()
            if task.done():
                _consume_task_result(task)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _consume_task_result(task: asyncio.Task) -> None:
    """读取后台 SSE 任务结果，避免客户端断开后出现未取异常。"""
    try:
        task.result()
    except BaseException:
        pass


@router.post("/run")
async def run_harness(payload: HarnessRunRequest) -> dict[str, Any]:
    """创建轮次、持久化运行现场，并执行到完成或等待用户确认。"""
    conversation_id = payload.conversation_id or str(uuid4())
    asset_ids = tuple(dict.fromkeys(payload.asset_ids))
    try:
        await _ensure_conversation(
            conversation_id=conversation_id,
            user_id=payload.user_id,
        )
        runtime, session_factory, llm_client = _require_runtime()
        run_ref = HarnessRunRef(
            user_id=payload.user_id,
            conversation_id=conversation_id,
            thread_id=conversation_id,
            turn_id=str(uuid4()),
            run_id=str(uuid4()),
        )
        meta_factory = meta_mysql_client_manager.session_factory
        dw_factory = dw_mysql_client_manager.session_factory
        if meta_factory is None or dw_factory is None:
            raise RuntimeError("Meta/DW Session 工厂尚未初始化")
        async with meta_factory() as meta_session, dw_factory() as dw_session:
            controller = _build_controller(
                run_ref=run_ref,
                asset_ids=asset_ids,
                runtime=runtime,
                session_factory=session_factory,
                llm_client=llm_client,
                meta_session=meta_session,
                dw_session=dw_session,
            )
            await ConversationRepository(session_factory).start_turn(
                conversation_id=conversation_id,
                user_id=payload.user_id,
                thread_id=conversation_id,
                turn_id=run_ref.turn_id,
                run_id=run_ref.run_id,
                input_text=payload.input_text,
            )
            try:
                result = await controller.start(
                    StartRunCommand(
                        run_ref=run_ref,
                        input_text=payload.input_text,
                        asset_ids=asset_ids,
                    )
                )
            except Exception:
                await _finish_unhandled_start_failure(
                    session_factory=session_factory,
                    run_ref=run_ref,
                )
                raise
        return _serialize_result(result)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/run/stream")
async def run_harness_stream(payload: HarnessRunRequest) -> StreamingResponse:
    """创建 Harness 运行并以 SSE 推送生命周期、工具和最终结果事件。"""
    conversation_id = payload.conversation_id or str(uuid4())
    asset_ids = tuple(dict.fromkeys(payload.asset_ids))
    try:
        await _ensure_conversation(
            conversation_id=conversation_id,
            user_id=payload.user_id,
        )
        runtime, session_factory, llm_client = _require_runtime()
        run_ref = HarnessRunRef(
            user_id=payload.user_id,
            conversation_id=conversation_id,
            thread_id=conversation_id,
            turn_id=str(uuid4()),
            run_id=str(uuid4()),
        )
        meta_factory = meta_mysql_client_manager.session_factory
        dw_factory = dw_mysql_client_manager.session_factory
        if meta_factory is None or dw_factory is None:
            raise RuntimeError("Meta/DW Session 工厂尚未初始化")

        queue: asyncio.Queue[Any] = asyncio.Queue()
        writer = HarnessEventWriter(run_ref=run_ref, sink=QueueEventSink(queue))

        async def operation() -> LoopResult:
            async with meta_factory() as meta_session, dw_factory() as dw_session:
                controller = _build_controller(
                    run_ref=run_ref,
                    asset_ids=asset_ids,
                    runtime=runtime,
                    session_factory=session_factory,
                    llm_client=llm_client,
                    meta_session=meta_session,
                    dw_session=dw_session,
                    event_writer=writer,
                )
                await ConversationRepository(session_factory).start_turn(
                    conversation_id=conversation_id,
                    user_id=payload.user_id,
                    thread_id=conversation_id,
                    turn_id=run_ref.turn_id,
                    run_id=run_ref.run_id,
                    input_text=payload.input_text,
                )
                return await controller.start(
                    StartRunCommand(
                        run_ref=run_ref,
                        input_text=payload.input_text,
                        asset_ids=asset_ids,
                    )
                )

        async def cleanup_failed_operation() -> None:
            await _finish_unhandled_start_failure(
                session_factory=session_factory,
                run_ref=run_ref,
            )

        return _sse_response(
            operation,
            run_ref=run_ref,
            queue=queue,
            writer=writer,
            on_operation_failure=cleanup_failed_operation,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/run/resume")
async def resume_harness(
    payload: HarnessResumeRequest,
) -> dict[str, Any]:
    """原子消费确认回复，并在同一 run、turn、thread 上继续执行。"""
    try:
        runtime, session_factory, llm_client = _require_runtime()
        run_store = PostgresHarnessRunStore(session_factory)
        run_id = payload.run_id
        user_id = payload.user_id
        state = await run_store.load_by_id(run_id=run_id, user_id=user_id)
        run_ref = _run_ref_from_state(state)
        reply = ConfirmationReply(
            confirmation_id=payload.confirmation_id,
            answer=payload.answer,
            decision=payload.decision,
            resolved_conditions=payload.resolved_conditions,
        )
        meta_factory = meta_mysql_client_manager.session_factory
        dw_factory = dw_mysql_client_manager.session_factory
        if meta_factory is None or dw_factory is None:
            raise RuntimeError("Meta/DW Session 工厂尚未初始化")
        async with meta_factory() as meta_session, dw_factory() as dw_session:
            controller = _build_controller(
                run_ref=run_ref,
                asset_ids=tuple(state.get("asset_ids", ())),
                runtime=runtime,
                session_factory=session_factory,
                llm_client=llm_client,
                meta_session=meta_session,
                dw_session=dw_session,
            )
            result = await controller.resume(
                ResumeRunCommand(run_ref=run_ref, reply=reply)
            )
        return _serialize_result(result)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/run/resume/stream")
async def resume_harness_stream(
    payload: HarnessResumeRequest,
) -> StreamingResponse:
    """恢复等待中的 Harness，并以 SSE 推送恢复后的完整生命周期。"""
    try:
        runtime, session_factory, llm_client = _require_runtime()
        run_store = PostgresHarnessRunStore(session_factory)
        run_id = payload.run_id
        user_id = payload.user_id
        state = await run_store.load_by_id(run_id=run_id, user_id=user_id)
        run_ref = _run_ref_from_state(state)
        reply = ConfirmationReply(
            confirmation_id=payload.confirmation_id,
            answer=payload.answer,
            decision=payload.decision,
            resolved_conditions=payload.resolved_conditions,
        )
        meta_factory = meta_mysql_client_manager.session_factory
        dw_factory = dw_mysql_client_manager.session_factory
        if meta_factory is None or dw_factory is None:
            raise RuntimeError("Meta/DW Session 工厂尚未初始化")

        queue: asyncio.Queue[Any] = asyncio.Queue()
        writer = HarnessEventWriter(run_ref=run_ref, sink=QueueEventSink(queue))

        async def operation() -> LoopResult:
            async with meta_factory() as meta_session, dw_factory() as dw_session:
                controller = _build_controller(
                    run_ref=run_ref,
                    asset_ids=tuple(state.get("asset_ids", ())),
                    runtime=runtime,
                    session_factory=session_factory,
                    llm_client=llm_client,
                    meta_session=meta_session,
                    dw_session=dw_session,
                    event_writer=writer,
                )
                return await controller.resume(
                    ResumeRunCommand(run_ref=run_ref, reply=reply)
                )

        async def cleanup_failed_operation() -> None:
            await _finish_unhandled_start_failure(
                session_factory=session_factory,
                run_ref=run_ref,
            )

        return _sse_response(
            operation,
            run_ref=run_ref,
            queue=queue,
            writer=writer,
            on_operation_failure=cleanup_failed_operation,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/run/reconcile")
async def reconcile_harness(payload: HarnessReconcileRequest) -> dict[str, Any]:
    """从收口账本当前阶段恢复未完成的收口；只重放收口，不回到执行链路。"""
    try:
        runtime, session_factory, _ = _require_runtime()
        finalization = PostgresFinalizationService(
            conversation_repository=ConversationRepository(session_factory),
            ledger=PostgresFinalizationLedger(session_factory),
            run_store=PostgresHarnessRunStore(session_factory),
            memory_formation_service=runtime.formation_service,
            artifact_store=PostgresResultArtifactStore(session_factory),
        )
        result = await finalization.reconcile(
            run_id=payload.run_id, user_id=payload.user_id
        )
        return result.model_dump(mode="json")
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/run/status")
async def get_harness_status(
    run_id: str = Query(..., min_length=1, max_length=128),
    user_id: str = Query(default=settings.app.default_user_id),
) -> dict[str, Any]:
    """查询运行状态和待处理确认，不返回完整上下文或工具结果。"""
    try:
        _, session_factory, _ = _require_runtime()
        state = await PostgresHarnessRunStore(session_factory).load_by_id(
            run_id=run_id,
            user_id=user_id,
        )
        return _status_payload(state)
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_error(exc) from exc


__all__ = [
    "HarnessReconcileRequest",
    "HarnessResumeRequest",
    "HarnessRunRequest",
    "router",
]
