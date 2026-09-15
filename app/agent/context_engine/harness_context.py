"""把 Harness 运行状态投影为 ContextEngine 可消费的受控 DTO。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.agent.state import HarnessGraphState

from app.agent.context_engine.contracts import CompiledContext, ContextRequest
from app.agent.context_engine.harness_context_contracts import (
    RuntimeCondition,
    RuntimeContext,
    RuntimeErrorSummary,
    RuntimeObservation,
    RuntimePlanProgress,
)
from app.agent.memory.enums import MemoryType
from app.agent.state_result_store.contracts import HarnessStateSnapshot


class RuntimeContextProjector(Protocol):
    def project(self, state: HarnessGraphState) -> RuntimeContext: ...


class ContextBuilder(Protocol):
    async def build(self, request: ContextRequest) -> CompiledContext: ...


class ContextRequestFactory(Protocol):
    def create(
        self,
        state: HarnessGraphState,
        *,
        system_instructions: str,
        agent_type: str = "general",
        memory_types: tuple[MemoryType, ...] | None = None,
        enable_rag: bool = False,
        token_budget: int | None = None,
    ) -> ContextRequest: ...


_IDENTITY_KEYS = frozenset(
    {"user_id", "conversation_id", "thread_id", "turn_id", "run_id"}
)


def _bounded_scalar(value: Any, *, limit: int = 1_000) -> str | None:
    """只允许可稳定转成短文本的标量进入运行态 Prompt。"""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (str, int, float)):
        text = str(value).strip()
    else:
        return None
    if not text or len(text) > limit:
        return None
    return text


def _project_conditions(values: Mapping[str, Any]) -> tuple[RuntimeCondition, ...]:
    conditions = []
    for key, value in values.items():
        key_text = str(key).strip()
        if (
            not key_text
            or len(key_text) > 128
            or key_text in _IDENTITY_KEYS
        ):
            continue
        value_text = _bounded_scalar(value)
        if value_text is not None:
            conditions.append(RuntimeCondition(key=key_text, value=value_text))
        if len(conditions) == 16:
            break
    return tuple(conditions)


def _project_observations(values: list[dict[str, Any]]) -> tuple[RuntimeObservation, ...]:
    result = []
    for value in values[-12:]:
        try:
            result.append(
                RuntimeObservation(
                    action_id=value["action_id"],
                    tool_name=value["tool_name"],
                    status=value["status"],
                    summary=value["summary"],
                    result_ref=value.get("result_ref"),
                    evidence_refs=tuple(value.get("evidence_refs", ())),
                    limitations=tuple(value.get("limitations", ())),
                    output_hash=value.get("output_hash"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(result)


def _project_errors(error: Mapping[str, Any] | None) -> tuple[RuntimeErrorSummary, ...]:
    if not error:
        return ()
    try:
        return (
            RuntimeErrorSummary(
                category=error["category"],
                code=error["code"],
                message=error["message"],
                retryable=bool(error.get("retryable", False)),
            ),
        )
    except (KeyError, TypeError, ValueError):
        return ()


class HarnessRuntimeContextProjector:
    """从已校验 Harness 状态创建无身份、有限大小的运行态视图。"""

    def project(self, state: HarnessGraphState) -> RuntimeContext:
        harness = state.get("harness")
        if not isinstance(harness, Mapping):
            raise ValueError("HarnessGraphState 缺少 harness 状态")
        snapshot = HarnessStateSnapshot.model_validate(harness)
        progress = snapshot.plan_progress
        return RuntimeContext(
            original_goal=snapshot.original_goal,
            last_confirmation_answer=snapshot.last_confirmation_answer,
            resolved_conditions=_project_conditions(snapshot.resolved_conditions),
            plan_progress=RuntimePlanProgress(
                goal_summary=progress.goal_summary,
                completed_steps=tuple(progress.completed_steps),
                pending_steps=tuple(progress.pending_steps),
                blocked_reason=progress.blocked_reason,
            ),
            observations=_project_observations(
                [observation.model_dump(mode="python") for observation in snapshot.observations]
            ),
            recent_errors=_project_errors(
                snapshot.last_error.model_dump(mode="python")
                if snapshot.last_error is not None
                else None
            ),
        )


class HarnessContextRequestFactory:
    """只映射 HarnessGraphState，不创建 ContextEngine 或外部依赖。"""

    def __init__(self, projector: RuntimeContextProjector | None = None) -> None:
        self.projector = projector or HarnessRuntimeContextProjector()

    def create(
        self,
        state: HarnessGraphState,
        *,
        system_instructions: str,
        agent_type: str = "general",
        memory_types: tuple[MemoryType, ...] | None = None,
        enable_rag: bool = False,
        token_budget: int | None = None,
    ) -> ContextRequest:
        return ContextRequest(
            user_id=state["user_id"],
            conversation_id=state["conversation_id"],
            query=state["input_text"],
            system_instructions=system_instructions,
            agent_type=agent_type,
            project_id=state.get("project_id"),
            asset_ids=tuple(state.get("asset_ids", ())),
            memory_types=memory_types,
            enable_rag=enable_rag,
            token_budget=token_budget,
            runtime_context=self.projector.project(state),
        )


__all__ = [
    "ContextBuilder",
    "ContextRequestFactory",
    "HarnessContextRequestFactory",
    "HarnessRuntimeContextProjector",
    "RuntimeContextProjector",
]
