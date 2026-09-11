"""Harness 控制状态及其合法转换。"""

from typing import Any, TypedDict

from app.agent.harness.contracts import (
    HarnessRunRef,
    HarnessStateSnapshot,
    HarnessStatus,
    LoopPhase,
)


class HarnessControlState(TypedDict, total=False):
    schema_version: int
    state_version: int
    checkpoint_revision: int
    fencing_token: int
    status: str
    phase: str
    action_seq: int
    iteration: int
    max_iterations: int
    planner_retry_count: int
    context_retry_count: int
    tool_retry_counts: dict[str, int]
    last_context_build_id: str | None
    last_context_token_count: int | None
    started_at: str | None
    deadline_at: str | None
    cancel_requested: bool
    terminal_intent: str | None
    original_goal: str
    plan_progress: dict[str, Any]
    observations: list[dict[str, Any]]
    resolved_conditions: dict[str, Any]
    pending_confirmation: dict[str, Any] | None
    last_error: dict[str, Any] | None


_DEFAULTS: HarnessControlState = {
    "schema_version": 1,
    "state_version": 0,
    "checkpoint_revision": 0,
    "fencing_token": 0,
    "status": HarnessStatus.RUNNING.value,
    "phase": LoopPhase.START_RUN.value,
    "iteration": 0,
    "action_seq": 0,
    "max_iterations": 8,
    "planner_retry_count": 0,
    "context_retry_count": 0,
    "tool_retry_counts": {},
    "last_context_build_id": None,
    "last_context_token_count": None,
    "started_at": None,
    "deadline_at": None,
    "cancel_requested": False,
    "terminal_intent": None,
    "original_goal": "",
    "plan_progress": {},
    "observations": [],
    "resolved_conditions": {},
    "pending_confirmation": None,
    "last_error": None,
}


_PHASE_TRANSITIONS = {
    LoopPhase.START_RUN: {LoopPhase.BUILD_CONTEXT, LoopPhase.FINALIZATION},
    LoopPhase.RESTORE_RUN: {LoopPhase.BUILD_CONTEXT, LoopPhase.FINALIZATION},
    LoopPhase.BUILD_CONTEXT: {
        LoopPhase.BUILD_CONTEXT,
        LoopPhase.PLAN,
        LoopPhase.FINALIZATION,
    },
    LoopPhase.PLAN: {
        LoopPhase.PLAN,
        LoopPhase.VALIDATE_ACTION,
        LoopPhase.FINALIZATION,
    },
    LoopPhase.VALIDATE_ACTION: {
        LoopPhase.VALIDATE_ACTION,
        LoopPhase.EXECUTE_TOOL,
        LoopPhase.WAIT_CONFIRMATION,
        LoopPhase.FINALIZATION,
    },
    LoopPhase.EXECUTE_TOOL: {
        LoopPhase.EXECUTE_TOOL,
        LoopPhase.HANDLE_TOOL_RESULT,
        LoopPhase.FINALIZATION,
    },
    LoopPhase.HANDLE_TOOL_RESULT: {
        LoopPhase.RECORD_OBSERVATION,
        LoopPhase.WAIT_CONFIRMATION,
        LoopPhase.FINALIZATION,
    },
    LoopPhase.RECORD_OBSERVATION: {
        LoopPhase.BUILD_CONTEXT,
        LoopPhase.FINALIZATION,
    },
    LoopPhase.WAIT_CONFIRMATION: {
        LoopPhase.RESTORE_RUN,
        LoopPhase.FINALIZATION,
    },
    LoopPhase.FINALIZATION: {LoopPhase.FINALIZATION},
}


def new_harness_control_state(
    *,
    original_goal: str,
    max_iterations: int = 8,
    started_at: str | None = None,
    deadline_at: str | None = None,
) -> HarnessControlState:
    """创建不共享可变容器的 Harness 初始状态。"""
    state = {
        **_DEFAULTS,
        "max_iterations": max_iterations,
        "started_at": started_at,
        "deadline_at": deadline_at,
        "tool_retry_counts": {},
        "plan_progress": {},
        "observations": [],
        "resolved_conditions": {},
        "original_goal": original_goal,
    }
    return decode_harness_state(state)


def transition_harness_state(
    state: HarnessControlState,
    *,
    status: HarnessStatus,
    phase: LoopPhase,
    terminal_intent: str | None = None,
) -> HarnessControlState:
    """执行阶段转换，并阻止业务阶段直接伪造终态。"""
    current = HarnessStateSnapshot.model_validate(state)
    terminal_statuses = {
        HarnessStatus.COMPLETED,
        HarnessStatus.FAILED,
        HarnessStatus.CANCELLED,
        HarnessStatus.TIMEOUT,
    }
    if current.status in terminal_statuses:
        if status is current.status and phase is LoopPhase.FINALIZATION:
            return encode_harness_state(state)
        raise ValueError(f"非法 Harness 状态转换: {current.status} -> {status}")
    if phase not in _PHASE_TRANSITIONS[current.phase]:
        raise ValueError(f"非法 Harness 阶段转换: {current.phase} -> {phase}")

    intent = terminal_intent if terminal_intent is not None else current.terminal_intent
    if status in terminal_statuses and (
        current.phase is not LoopPhase.FINALIZATION or status.value != intent
    ):
        raise ValueError("终态只能由 running/finalization 的同名 intent 提交")

    candidate = {
        **encode_harness_state(state),
        "status": status.value,
        "phase": phase.value,
        "terminal_intent": intent,
        "state_version": current.state_version + 1,
    }
    if current.status is HarnessStatus.WAITING_CONFIRMATION and status is HarnessStatus.RUNNING:
        candidate["pending_confirmation"] = None
    return decode_harness_state(candidate)


def encode_harness_state(state: HarnessControlState) -> dict[str, Any]:
    """校验控制状态并生成 JSON-safe checkpoint payload。"""
    return HarnessStateSnapshot.model_validate(state).model_dump(mode="json")


def decode_harness_state(payload: dict[str, Any]) -> HarnessControlState:
    """拒绝未知 schema 或非法组合，并恢复为控制状态字典。"""
    return HarnessStateSnapshot.model_validate(payload).model_dump(mode="json")


def restore_harness_state(
    state: dict[str, Any], ref: HarnessRunRef
) -> dict[str, Any]:
    """校验进程恢复身份和状态，不创建新的运行身份。"""
    for field, expected in ref.model_dump().items():
        if state.get(field) != expected:
            raise ValueError(f"Harness 恢复身份不匹配: {field}")
    harness = decode_harness_state(state.get("harness", {}))
    if HarnessStatus(harness["status"]) is not HarnessStatus.RUNNING:
        raise ValueError("普通恢复只允许 running 或 running/finalization 状态")
    return {**state, "harness": harness}


__all__ = [
    "HarnessControlState",
    "decode_harness_state",
    "encode_harness_state",
    "new_harness_control_state",
    "restore_harness_state",
    "transition_harness_state",
]
