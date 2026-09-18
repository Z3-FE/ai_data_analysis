"""Harness 模块间的最小、可序列化 DTO。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.context_engine.contracts import CompiledContext


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
    ASK_USER = "ask_user"
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


class ConfirmationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ConfirmationVisibility(StrEnum):
    PREPARED = "prepared"
    PUBLISHED = "published"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HarnessRunRef(ContractModel):
    """一次 Harness 运行的完整身份五元组；所有状态存取、事件归属、确认恢复、收口对账都凭它定位。"""

    user_id: str = Field(min_length=1)  # 运行归属用户（当前未接登录，默认 default_user_id）
    conversation_id: str = Field(min_length=1)  # 会话 —— 前端聊天列表里的一个会话
    thread_id: str = Field(min_length=1)  # 线程 —— 当前实现恒等于 conversation_id（run/resume 入口处），为一会话多线程预留
    turn_id: str = Field(min_length=1)  # 轮次 —— 会话中一次问答，start_turn 时创建
    run_id: str = Field(min_length=1)  # 运行 —— 一次 Harness 控制器执行；暂停后 resume 仍是同一个 run


class HarnessRequest(ContractModel):
    mode: Literal["new", "restore"]
    input_text: str | None = Field(default=None, min_length=1)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    run_ref: HarnessRunRef | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "HarnessRequest":
        if self.mode == "new" and not self.input_text:
            raise ValueError("new run 必须提供 input_text")
        if self.mode == "new" and self.run_ref is not None:
            raise ValueError("new run 不能携带 run_ref")
        if self.mode == "restore" and self.run_ref is None:
            raise ValueError("restore 必须提供 run_ref")
        if self.mode == "restore" and (
            self.input_text is not None or self.project_id is not None or self.asset_ids
        ):
            raise ValueError("restore 不能覆盖原请求、项目或附件范围")
        return self


class RunExecutionFence(ContractModel):
    """Worker 的短期执行权，不是用户授权凭证。"""

    owner_id: str = Field(min_length=1, max_length=128)
    fencing_token: int = Field(ge=1)
    lease_expires_at: datetime


class RunError(ContractModel):
    category: ErrorCategory
    code: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False
    action_id: str | None = Field(default=None, min_length=1)


class RunObservation(ContractModel):
    observation_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    summary: str = Field(min_length=1, max_length=4_000)
    result_ref: str | None = Field(default=None, min_length=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=32)
    output_hash: str | None = Field(default=None, min_length=1)
    # 工具名和规范化参数的 SHA-256；用于阻止同一运行重复执行已失败请求。
    request_hash: str | None = Field(default=None, min_length=64, max_length=64)


class AskUserRequest(ContractModel):
    question: str = Field(min_length=1, max_length=2_000)
    reason_code: Literal[
        "missing_condition",
        "ambiguous_reference",
        "conflicting_definition",
        "tool_needs_user",
    ]
    required_fields: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_required_fields(self) -> "AskUserRequest":
        if any(not field.strip() for field in self.required_fields):
            raise ValueError("required_fields 不能包含空字段名")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields 不能重复")
        return self


class ConfirmationRequest(ContractModel):
    confirmation_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    choices: list[str] = Field(default_factory=list, max_length=16)
    required_fields: tuple[str, ...] = Field(default=(), max_length=16)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> "ConfirmationRequest":
        if any(not field.strip() for field in self.required_fields):
            raise ValueError("required_fields 不能包含空字段名")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields 不能重复")
        return self


class ConfirmationReply(ContractModel):
    confirmation_id: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4_000)
    decision: Literal["confirm", "reject"] = "confirm"
    resolved_conditions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_decision(self) -> "ConfirmationReply":
        if self.decision == "reject" and self.resolved_conditions:
            raise ValueError("reject 回复不能携带 resolved_conditions")
        return self


class ConfirmationRecord(ContractModel):
    run_ref: HarnessRunRef
    request: ConfirmationRequest
    request_digest: str = Field(min_length=64, max_length=64)
    status: ConfirmationStatus = ConfirmationStatus.PENDING
    visibility: ConfirmationVisibility = ConfirmationVisibility.PREPARED
    reply: ConfirmationReply | None = None
    reply_digest: str | None = Field(default=None, min_length=64, max_length=64)
    prepared_at: datetime
    published_at: datetime | None = None
    resolved_at: datetime | None = None


class ConfirmationResolution(ContractModel):
    """确认消费结果；idempotent 表示同一回复已经成功处理。"""

    status: Literal["confirmed", "rejected", "idempotent"]
    state: dict[str, Any]


class ToolSpec(ContractModel):
    # 工具对外暴露的稳定名称。
    name: str = Field(min_length=1)
    # Planner 用来判断工具适用场景的描述。
    description: str = Field(min_length=1)
    # Planner 生成 tool_call.arguments 时使用的 JSON Schema。
    input_schema: dict[str, Any] = Field(default_factory=dict)
    # 执行该工具所需的权限标识。
    permission: str = Field(min_length=1)
    # 工具契约版本；动作记录和结果审计需要保留它。
    version: str = Field(default="v1", min_length=1)
    # 是否允许当前 Harness 暴露并执行该工具。
    enabled: bool = True
    # 单次工具调用的最大执行时间。
    timeout_seconds: int = Field(default=60, gt=0)
    # 工具执行结果是否可以安全重试。
    idempotency: Literal[
        "idempotent", "conditionally_idempotent", "non_idempotent"
    ] = "idempotent"
    # 工具超时后是否允许自动重放；昂贵链路通常应关闭，避免重复执行。
    retry_on_timeout: bool = True
    # 结果向 Planner 暴露的方式；只有 artifact/report 才要求持久化完整结果。
    result_kind: Literal["inline_summary", "artifact", "report"] = "inline_summary"
    # Artifact 在 PostgreSQL 中的业务类型；为空时由运行时使用稳定默认值。
    artifact_kind: str | None = Field(default=None, min_length=1, max_length=64)


class ToolCall(ContractModel):
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0)


class PlanProgress(ContractModel):
    goal_summary: str = Field(default="", max_length=2_000)
    completed_steps: list[str] = Field(default_factory=list, max_length=32)
    pending_steps: list[str] = Field(default_factory=list, max_length=32)
    blocked_reason: str | None = Field(default=None, max_length=1_000)


class HarnessStateSnapshot(ContractModel):
    schema_version: Literal[1] = 1
    state_version: int = Field(default=0, ge=0)
    checkpoint_revision: int = Field(default=0, ge=0)
    fencing_token: int = Field(default=0, ge=0)
    status: HarnessStatus = HarnessStatus.RUNNING
    phase: LoopPhase = LoopPhase.START_RUN
    iteration: int = Field(default=0, ge=0)
    action_seq: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=8, gt=0)
    planner_retry_count: int = Field(default=0, ge=0)
    context_retry_count: int = Field(default=0, ge=0)
    tool_retry_counts: dict[str, int] = Field(default_factory=dict)
    last_context_build_id: str | None = None
    last_context_token_count: int | None = Field(default=None, ge=0)
    started_at: datetime | None = None
    deadline_at: datetime | None = None
    cancel_requested: bool = False
    terminal_intent: Literal["completed", "failed", "cancelled", "timeout"] | None = None
    original_goal: str = Field(min_length=1)
    plan_progress: PlanProgress = Field(default_factory=PlanProgress)
    observations: list[RunObservation] = Field(default_factory=list)
    resolved_conditions: dict[str, Any] = Field(default_factory=dict)
    # 最近一次确认的自然语言回复；只作为当前 run 的运行态，不修改全局记忆或身份。
    last_confirmation_answer: str | None = Field(default=None, max_length=4_000)
    # 当前运行已经消费过的用户确认次数，用于限制重复澄清。
    confirmation_attempt_count: int = Field(default=0, ge=0)
    # 最近一次已消费确认的原因，用于识别相同确认请求循环。
    last_confirmation_reason_code: str | None = Field(default=None, max_length=64)
    # 最近一次已消费确认的问题，用于识别相同确认请求循环。
    last_confirmation_question: str | None = Field(default=None, max_length=2_000)
    pending_confirmation: ConfirmationRequest | None = None
    final_answer: str | None = Field(default=None, max_length=20_000)
    last_error: RunError | None = None

    @model_validator(mode="after")
    def validate_combination(self) -> "HarnessStateSnapshot":
        terminal = {
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
            HarnessStatus.TIMEOUT,
        }
        if self.deadline_at and self.started_at and self.deadline_at < self.started_at:
            raise ValueError("deadline_at 不能早于 started_at")
        if self.status is HarnessStatus.WAITING_CONFIRMATION:
            if (
                self.phase is not LoopPhase.WAIT_CONFIRMATION
                or self.terminal_intent
                or self.pending_confirmation is None
            ):
                raise ValueError("waiting_confirmation 状态组合非法")
        elif self.status in terminal:
            if self.phase is not LoopPhase.FINALIZATION:
                raise ValueError("终态必须使用 finalization 阶段")
            if self.terminal_intent != self.status.value:
                raise ValueError("终态必须与 terminal_intent 一致")
            if self.pending_confirmation is not None:
                raise ValueError("终态不能保留 pending_confirmation")
        elif self.phase is LoopPhase.WAIT_CONFIRMATION:
            raise ValueError("wait_confirmation 阶段必须处于等待确认状态")
        elif self.phase is LoopPhase.FINALIZATION:
            if self.terminal_intent is None:
                raise ValueError("finalization 阶段必须提供 terminal_intent")
        elif self.terminal_intent is not None:
            raise ValueError("普通运行阶段不能携带 terminal_intent")
        return self


class CheckpointCodec(Protocol):
    def encode_harness(self, state: dict[str, Any]) -> dict[str, Any]: ...

    def decode_harness(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class PlannerStateView(ContractModel):
    original_goal: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    plan_progress: PlanProgress = Field(default_factory=PlanProgress)
    observations: list[RunObservation] = Field(default_factory=list)
    last_error: RunError | None = None


class PlannerCapabilities(ContractModel):
    allow_tool_call: bool = True
    allow_ask_user: bool = False
    allow_final_answer: bool = True
    allow_context_only_final_answer: bool = False


class PlannerInput(ContractModel):
    compiled_context: CompiledContext
    state_view: PlannerStateView
    tool_specs: tuple[ToolSpec, ...]


class NextAction(ContractModel):
    action_seq: int = Field(ge=1)
    action_type: ActionType
    tool_call: ToolCall | None = None
    ask_user: AskUserRequest | None = None
    final_answer: str | None = Field(default=None, min_length=1, max_length=20_000)
    rationale_summary: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_payload(self) -> "NextAction":
        payload_count = sum(
            value is not None
            for value in (self.tool_call, self.ask_user, self.final_answer)
        )
        if payload_count != 1:
            raise ValueError("NextAction 必须且只能包含一个动作 payload")
        if self.action_type is ActionType.TOOL_CALL and self.tool_call is None:
            raise ValueError("tool_call 动作必须包含 tool_call")
        if self.action_type is ActionType.ASK_USER and self.ask_user is None:
            raise ValueError("ask_user 动作必须包含 ask_user")
        if self.action_type is ActionType.FINAL_ANSWER and not self.final_answer:
            raise ValueError("final_answer 动作必须包含非空 final_answer")
        return self


class ToolResult(ContractModel):
    # tool_call_id 是 ToolCall.action_id 的协议别名。
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    summary: str = Field(min_length=1, max_length=4_000)
    result_ref: str | None = Field(default=None, min_length=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=32)
    # 完整结果 Artifact 的内容哈希；没有 Artifact 时为空。
    output_hash: str | None = Field(default=None, min_length=64, max_length=64)
    error_category: ErrorCategory | None = None
    error_code: str | None = Field(default=None, min_length=1)
    error_message: str | None = Field(default=None, min_length=1)
    retryable: bool = False
    confirmation_request: AskUserRequest | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "ToolResult":
        error_values = (self.error_category, self.error_code, self.error_message)
        has_complete_error = all(value is not None for value in error_values)
        has_partial_error = any(value is not None for value in error_values)
        error_status = self.status in {
            ResultStatus.TEMPORARY_ERROR,
            ResultStatus.NEEDS_USER,
            ResultStatus.UNRECOVERABLE_ERROR,
        }
        if has_partial_error and not has_complete_error:
            raise ValueError("ToolResult 错误字段必须同时存在")
        if has_complete_error != error_status:
            raise ValueError("ToolResult 的错误字段必须与 status 一致")
        if self.status is ResultStatus.NEEDS_USER and self.confirmation_request is None:
            raise ValueError("needs_user 结果必须携带 confirmation_request")
        if self.status is not ResultStatus.NEEDS_USER and self.confirmation_request is not None:
            raise ValueError("只有 needs_user 结果可以携带 confirmation_request")
        if self.status is ResultStatus.SUCCESS and self.retryable:
            raise ValueError("success 结果不能标记为 retryable")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at 不能早于 started_at")
        return self


__all__ = [
    "ActionType",
    "AskUserRequest",
    "CheckpointCodec",
    "ConfirmationRecord",
    "ConfirmationResolution",
    "ConfirmationReply",
    "ConfirmationRequest",
    "ConfirmationStatus",
    "ConfirmationVisibility",
    "ContractModel",
    "ErrorCategory",
    "HarnessRequest",
    "HarnessRunRef",
    "HarnessStateSnapshot",
    "HarnessStatus",
    "LoopPhase",
    "NextAction",
    "PlanProgress",
    "PlannerCapabilities",
    "PlannerInput",
    "PlannerStateView",
    "ResultStatus",
    "RunError",
    "RunExecutionFence",
    "RunObservation",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
]
