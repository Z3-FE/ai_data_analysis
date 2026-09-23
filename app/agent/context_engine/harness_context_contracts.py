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
    """Harness 运行现场的无身份、有限大小视图，随 ContextRequest 进编译与规划。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # 本 run 的原始目标（用户最初的问题）：规划与上下文构建的锚点，全程不重写。
    original_goal: str = Field(min_length=1, max_length=8_000)
    # 最近一次用户确认的自然语言答案；只对当前 Harness run 生效。
    last_confirmation_answer: ConfirmationAnswer | None = None
    # 用户确认时敲定的 key=value 条件；投影端剔除身份键、标量限长后最多 16 条。
    resolved_conditions: tuple[RuntimeCondition, ...] = Field(
        default_factory=tuple, max_length=16
    )
    # 规划进度投影：目标摘要、已完成/待办步骤、阻塞原因——Planner 重规划时的进度参照。
    plan_progress: RuntimePlanProgress = Field(default_factory=RuntimePlanProgress)
    # 最近工具观察窗口：投影端只取末尾 12 条（多则丢最旧）；含状态/摘要/产物引用/证据/局限。
    observations: tuple[RuntimeObservation, ...] = Field(
        default_factory=tuple, max_length=12
    )
    # 最近错误摘要：上限 4 条，当前投影端只投 last_error 一条——Planner 判断重试/换路的依据。
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
