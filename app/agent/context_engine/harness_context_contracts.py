"""Harness 到 ContextEngine 的受控运行态投影 DTO。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

RuntimeKey = Annotated[str, Field(min_length=1, max_length=128)]
RuntimeValue = Annotated[str, Field(min_length=1, max_length=1_000)]
ConfirmationAnswer = Annotated[str, Field(min_length=1, max_length=4_000)]
PlanStep = Annotated[str, Field(min_length=1, max_length=256)]
ArtifactRef = Annotated[str, Field(min_length=1, max_length=256)]
Digest = Annotated[str, Field(min_length=1, max_length=128)]


class RuntimeCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: RuntimeKey
    value: RuntimeValue


class RuntimeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: RuntimeKey
    tool_name: RuntimeKey
    status: Literal[
        "success",
        "partial",
        "temporary_error",
        "needs_user",
        "unrecoverable_error",
    ]
    summary: RuntimeValue
    result_ref: ArtifactRef | None = None
    evidence_refs: tuple[ArtifactRef, ...] = Field(default_factory=tuple, max_length=32)
    limitations: tuple[RuntimeValue, ...] = Field(default_factory=tuple, max_length=16)
    output_hash: Digest | None = None


class RuntimeErrorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal[
        "validation",
        "planner",
        "context",
        "tool",
        "database",
        "timeout",
        "permission",
        "user_input",
        "conflict",
        "cancelled",
        "unknown",
    ]
    code: RuntimeKey
    message: RuntimeValue
    retryable: bool = False


class RuntimePlanProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_summary: str = Field(default="", max_length=2_000)
    completed_steps: tuple[PlanStep, ...] = Field(default_factory=tuple, max_length=32)
    pending_steps: tuple[PlanStep, ...] = Field(default_factory=tuple, max_length=32)
    blocked_reason: RuntimeValue | None = None


class RuntimeContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_goal: str = Field(min_length=1, max_length=8_000)
    # 最近一次用户确认的自然语言答案；只对当前 Harness run 生效。
    last_confirmation_answer: ConfirmationAnswer | None = None
    resolved_conditions: tuple[RuntimeCondition, ...] = Field(
        default_factory=tuple, max_length=16
    )
    plan_progress: RuntimePlanProgress = Field(default_factory=RuntimePlanProgress)
    observations: tuple[RuntimeObservation, ...] = Field(
        default_factory=tuple, max_length=12
    )
    recent_errors: tuple[RuntimeErrorSummary, ...] = Field(
        default_factory=tuple, max_length=4
    )


__all__ = [
    "ArtifactRef",
    "ConfirmationAnswer",
    "Digest",
    "PlanStep",
    "RuntimeCondition",
    "RuntimeContext",
    "RuntimeErrorSummary",
    "RuntimeKey",
    "RuntimeObservation",
    "RuntimePlanProgress",
    "RuntimeValue",
]
