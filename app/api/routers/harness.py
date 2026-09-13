"""Harness 的本地 HTTP 运行入口。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.agent.business_tools.query_data import QueryDataTool
from app.agent.context import AgentContext
from app.agent.context_engine.factory import build_context_engine
from app.agent.context_engine.harness_context import HarnessContextRequestFactory
from app.agent.loop_controller.action_commit import (
    ActionCommitRequest,
    ActionCommitResult,
)
from app.agent.loop_controller.contracts import (
    FinalizationInput,
    FinalizationResult,
    StartRunCommand,
)
from app.agent.loop_controller.controller import LoopController
from app.agent.planning_agent.agent import AutoLLMPlannerClient, PlanningAgent
from app.agent.state import HarnessGraphState
from app.agent.state_result_store.contracts import (
    HarnessRunRef,
    HarnessStatus,
    NextAction,
    PlannerCapabilities,
    ToolSpec,
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


class HarnessRunRequest(BaseModel):
    """Harness 调试请求。"""

    input_text: str = Field(min_length=1, max_length=20_000)
    user_id: str = Field(default="debug-user", min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: list[str] = Field(default_factory=list, max_length=32)


class _DebugRunStore:
    """请求内状态快照；D/E 再替换为持久化实现。"""

    def __init__(self) -> None:
        self.states: dict[str, HarnessGraphState] = {}
        self.snapshots: list[HarnessGraphState] = []

    async def create(self, run_ref: HarnessRunRef, state: HarnessGraphState) -> None:
        if run_ref.run_id in self.states:
            raise ValueError(f"run 已存在: {run_ref.run_id}")
        self.states[run_ref.run_id] = deepcopy(state)
        self.snapshots.append(deepcopy(state))

    async def save(self, run_ref: HarnessRunRef, state: HarnessGraphState) -> None:
        if run_ref.run_id not in self.states:
            raise ValueError(f"run 不存在: {run_ref.run_id}")
        self.states[run_ref.run_id] = deepcopy(state)
        self.snapshots.append(deepcopy(state))

    async def load(self, run_ref: HarnessRunRef) -> HarnessGraphState:
        return deepcopy(self.states[run_ref.run_id])


class _DebugActionCommitter:
    """切片 C 暂用的可观察提交实现；D/E 再接持久化动作提交。"""

    def __init__(self) -> None:
        self.calls: list[ActionCommitRequest] = []
        self.events: list[dict[str, Any]] = []
        self.results: list[ActionCommitResult] = []

    async def commit(self, request: ActionCommitRequest) -> ActionCommitResult:
        self.calls.append(request)
        for stage in ("prepared", "checkpoint", "committed"):
            self.events.append({"stage": stage, "action_seq": request.action.action_seq})
        action_id = request.action.tool_call.action_id if request.action.tool_call else None
        result = ActionCommitResult(status="committed", action_seq=request.action.action_seq, action_type=request.action.action_type, action_id=action_id)
        self.results.append(result)
        return result


class _DebugConfirmationDispatcher:
    """B 的即时确认替身；暂停恢复属于 D。"""

    def __init__(self) -> None:
        self.calls: list[NextAction] = []

    async def dispatch(self, value: NextAction) -> str:
        self.calls.append(value)
        return "调试确认分派已接收。"


class _HarnessFinalization:
    """A-C 的临时收口；E 再替换为 M6 FinalizationService。"""

    def __init__(self) -> None:
        self.calls: list[FinalizationInput] = []

    async def finalize(self, value: FinalizationInput) -> FinalizationResult:
        self.calls.append(value)
        return FinalizationResult(run_ref=value.run_ref, status=HarnessStatus.COMPLETED, final_answer=value.final_answer)


def _require_runtime() -> tuple[Any, Any, Any]:
    """读取应用生命周期初始化的真实依赖。"""
    runtime = memory_client_manager.runtime
    session_factory = postgres_client_manager.session_factory
    llm_client = llm_client_manager.client
    if runtime is None:
        raise RuntimeError("Memory Runtime 尚未初始化，请通过应用生命周期启动服务")
    if session_factory is None:
        raise RuntimeError("PostgreSQL Session 工厂尚未初始化，请通过应用生命周期启动服务")
    if llm_client is None:
        raise RuntimeError("LLM 客户端尚未初始化，请通过应用生命周期启动服务")
    return runtime.manager, session_factory, llm_client


async def _ensure_conversation(*, conversation_id: str, user_id: str) -> dict[str, Any]:
    _, session_factory, _ = _require_runtime()
    return await ConversationRepository(session_factory=session_factory).ensure_conversation(conversation_id=conversation_id, user_id=user_id)


def _context_payload(value: Any) -> dict[str, Any]:
    return asdict(value)


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
    )


@router.post("/run")
async def run_harness(payload: HarnessRunRequest) -> dict[str, Any]:
    """执行切片 C 的真实 Tool Runtime 查询闭环。"""
    conversation_id = payload.conversation_id or f"debug-conversation-{uuid4()}"
    await _ensure_conversation(conversation_id=conversation_id, user_id=payload.user_id)
    memory_reader, postgres_factory, llm_client = _require_runtime()
    meta_factory = meta_mysql_client_manager.session_factory
    dw_factory = dw_mysql_client_manager.session_factory
    if meta_factory is None or dw_factory is None:
        raise RuntimeError("Meta/DW Session 工厂尚未初始化")

    run_ref = HarnessRunRef(user_id=payload.user_id, conversation_id=conversation_id, thread_id=conversation_id, turn_id=f"debug-turn-{uuid4()}", run_id=f"debug-run-{uuid4()}")
    async with meta_factory() as meta_session, dw_factory() as dw_session:
        context = _agent_context(meta_session=meta_session, dw_session=dw_session, llm_client=llm_client)
        query_tool = QueryDataTool(context=context, run_ref=run_ref, asset_ids=tuple(payload.asset_ids))
        tool_spec = ToolSpec(
            name="query_data",
            description="使用现有问数链路查询数据",
            permission="data.query.read",
            input_schema={"type": "object", "required": ["query"], "properties": {"query": {"type": "string", "minLength": 1}}, "additionalProperties": False},
        )
        tool_runtime = ToolRuntime(ToolRegistry({"query_data": (tool_spec, query_tool)}))
        action_committer = _DebugActionCommitter()
        confirmation_dispatcher = _DebugConfirmationDispatcher()
        planning_agent = PlanningAgent(llm_client=AutoLLMPlannerClient(llm_client), capabilities=PlannerCapabilities(allow_ask_user=False))
        finalization = _HarnessFinalization()
        run_store = _DebugRunStore()
        controller = LoopController(
            context_builder=build_context_engine(memory_reader=memory_reader, session_factory=postgres_factory),
            planning_agent=planning_agent,
            finalization_service=finalization,
            run_store=run_store,
            context_request_factory=HarnessContextRequestFactory(),
            action_committer=action_committer,
            tool_runtime=tool_runtime,
            confirmation_dispatcher=confirmation_dispatcher,
            tool_specs=(tool_spec,),
            continue_after_tool=True,
        )
        result = await controller.start(StartRunCommand(run_ref=run_ref, input_text=payload.input_text, asset_ids=tuple(payload.asset_ids)))

    planner_input = planning_agent.calls[-1]
    committed_action = action_committer.calls[-1].action
    finalization_input = finalization.calls[-1]
    return {
        "run_ref": run_ref.model_dump(mode="json"),
        "status": result.status,
        "phase": result.phase,
        "iteration": result.iteration,
        "final_answer": result.finalization_result.final_answer,
        "planner_action": committed_action.model_dump(mode="json"),
        "planner_call_count": len(planning_agent.calls),
        "planner_input": {"state_view": planner_input.state_view.model_dump(mode="json"), "tool_specs": [spec.model_dump(mode="json") for spec in planner_input.tool_specs], "compiled_context_build_id": planner_input.compiled_context.build_id},
        "context": _context_payload(planner_input.compiled_context),
        "action_commit_events": action_committer.events,
        "action_commit_result": action_committer.results[-1].model_dump(mode="json"),
        "tool_execution_request": tool_runtime.calls[-1].model_dump(mode="json") if tool_runtime.calls else None,
        "tool_call_count": len(tool_runtime.calls),
        "confirmation_dispatch_count": len(confirmation_dispatcher.calls),
        "finalization_input": {"run_ref": finalization_input.run_ref.model_dump(mode="json"), "user_query": finalization_input.user_query, "compiled_context_build_id": finalization_input.compiled_context.build_id, "final_answer": finalization_input.final_answer},
        "snapshots": run_store.snapshots,
    }


__all__ = ["router"]
