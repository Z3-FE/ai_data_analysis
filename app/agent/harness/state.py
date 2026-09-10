"""Harness 控制状态及其合法转换。"""

from typing import Any, TypedDict

from app.agent.harness.contracts import HarnessStatus, LoopPhase


class HarnessControlState(TypedDict, total=False):
    status: str
    phase: str
    iteration: int
    max_iterations: int
    planner_retry_count: int
    context_retry_count: int
    tool_retry_counts: dict[str, int]
    original_goal: str
    plan_progress: dict[str, Any]
    observations: list[dict[str, Any]]
    pending_confirmation: dict[str, Any] | None
    last_error: dict[str, Any] | None


_DEFAULTS: HarnessControlState = {
    "status": HarnessStatus.RUNNING.value,
    "phase": LoopPhase.START_RUN.value,
    "iteration": 0,
    "max_iterations": 8,
    "planner_retry_count": 0,
    "context_retry_count": 0,
    "tool_retry_counts": {},
    "original_goal": "",
    "plan_progress": {},
    "observations": [],
    "pending_confirmation": None,
    "last_error": None,
}


def new_harness_control_state(*, original_goal: str = "") -> HarnessControlState:
    """创建不共享可变容器的 Harness 初始状态。"""
    state = {
        **_DEFAULTS,
        "tool_retry_counts": {},
        "plan_progress": {},
        "observations": [],
        "original_goal": original_goal,
    }
    return state


def transition_harness_state(
    state: HarnessControlState, *, status: HarnessStatus, phase: LoopPhase
) -> HarnessControlState:
    """执行严格的 Harness 状态转换。"""
    current = HarnessStatus(state.get("status", HarnessStatus.RUNNING.value))
    allowed = {
        HarnessStatus.RUNNING: {
            HarnessStatus.WAITING_CONFIRMATION,
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
            HarnessStatus.TIMEOUT,
        },
        HarnessStatus.WAITING_CONFIRMATION: {
            HarnessStatus.RUNNING,
            HarnessStatus.CANCELLED,
        },
    }
    if status != current and status not in allowed.get(current, set()):
        raise ValueError(f"非法 Harness 状态转换: {current} -> {status}")
    return {**state, "status": status.value, "phase": phase.value}


__all__ = ["HarnessControlState", "new_harness_control_state", "transition_harness_state"]
