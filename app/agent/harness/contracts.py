"""Harness 模块间的最小、可序列化 DTO。"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HarnessStatus(StrEnum):
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class LoopPhase(StrEnum):
    START_RUN = "start_run"
    RESTORE_RUN = "restore_run"
    BUILD_CONTEXT = "build_context"
    PLAN = "plan"
    VALIDATE_ACTION = "validate_action"
    EXECUTE_TOOL = "execute_tool"
    HANDLE_TOOL_RESULT = "handle_tool_result"
    RECORD_OBSERVATION = "record_observation"
    WAIT_CONFIRMATION = "wait_confirmation"
    FINALIZATION = "finalization"


class ActionType(StrEnum):
    TOOL_CALL = "tool_call"
    FINAL_ANSWER = "final_answer"


class ResultStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    TEMPORARY_ERROR = "temporary_error"
    NEEDS_USER = "needs_user"
    UNRECOVERABLE_ERROR = "unrecoverable_error"


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    PLANNER = "planner"
    CONTEXT = "context"
    TOOL = "tool"
    DATABASE = "database"
    TIMEOUT = "timeout"
    PERMISSION = "permission"
    USER_INPUT = "user_input"
    CONFLICT = "conflict"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunError(ContractModel):
    category: ErrorCategory
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    action_id: str | None = Field(default=None, min_length=1)


class RunObservation(ContractModel):
    observation_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    summary: str = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    output_hash: str | None = None


class ToolCall(ContractModel):
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0)


class ToolSpec(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    permission: str = Field(min_length=1)


class PlannerStateView(ContractModel):
    original_goal: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    plan_progress: dict[str, Any] = Field(default_factory=dict)
    observations: list[RunObservation] = Field(default_factory=list)
    last_error: RunError | None = None


class PlannerInput(ContractModel):
    context: Any
    state_view: PlannerStateView
    tools: list[ToolSpec] = Field(default_factory=list)


class NextAction(ContractModel):
    action_type: ActionType
    tool_call: ToolCall | None = None
    final_answer: str | None = None
    rationale_summary: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "NextAction":
        if self.action_type is ActionType.TOOL_CALL:
            if self.tool_call is None or self.final_answer is not None:
                raise ValueError("tool_call 动作必须只包含 tool_call")
        elif self.action_type is ActionType.FINAL_ANSWER:
            if self.tool_call is not None or not self.final_answer:
                raise ValueError("final_answer 动作必须只包含非空 final_answer")
        return self


class ToolResult(ContractModel):
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    output: dict[str, Any] | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    error: RunError | None = None
    duration_ms: int | None = Field(default=None, ge=0)


__all__ = [
    "ActionType", "ErrorCategory", "HarnessStatus", "LoopPhase",
    "NextAction", "PlannerInput", "PlannerStateView", "ResultStatus",
    "RunError", "RunObservation", "ToolCall", "ToolResult", "ToolSpec",
]
