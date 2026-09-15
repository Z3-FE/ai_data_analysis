"""切片 A 的最小运行编排契约。

这些 DTO 只覆盖从 start 到 completed 的纵向闭环。后续切片可以复用
``PlannerInput``、``NextAction`` 和 ``CompiledContext``，再将收口 DTO
迁移到正式的 M6 模块，而不改变 LoopController 的依赖边界。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import Field, model_validator

from app.agent.context_engine.contracts import CompiledContext, ContextRequest
from app.agent.planning_agent.contracts import ActionIssuanceContext
from app.agent.state_result_store.contracts import (
    ConfirmationRecord,
    ConfirmationReply,
    ConfirmationRequest,
    ConfirmationResolution,
    ContractModel,
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
    NextAction,
    PlannerInput,
    RunError,
)
from app.agent.tool_runtime.contracts import ToolExecutionRequest, ToolRuntime

if TYPE_CHECKING:
    from app.agent.state import HarnessGraphState


class StartRunCommand(ContractModel):
    """创建一个新的 Harness run；身份由调用方提供。"""

    run_ref: HarnessRunRef
    input_text: str = Field(min_length=1, max_length=20_000)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)


class FinalizationInput(ContractModel):
    """交给最终收口的受控输入。

    运行现场不由调用方搬运；收口服务通过 run_store 读取
    running/finalization 现场并推进到终态。
    """

    run_ref: HarnessRunRef
    user_query: str = Field(min_length=1, max_length=20_000)
    # 正常完成来自最近一次 ContextEngine 构建；运行超时或取消时可能尚未构建。
    compiled_context: CompiledContext | None = None
    final_answer: str = Field(min_length=1, max_length=20_000)
    terminal_status: HarnessStatus = HarnessStatus.COMPLETED
    error_message: str = Field(default="", max_length=2_000)
    # 本轮关联附件；记忆形成需要它识别感知记忆候选。
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    # 本轮结构化输出类型；默认只保存文字结果。
    final_output_type: str = Field(default="text", min_length=1, max_length=64)
    # 结构化输出的完整内容引用；大对象不进入收口账本和运行现场。
    final_output_ref: str | None = Field(default=None, min_length=1, max_length=256)


class FinalizationResult(ContractModel):
    """收口结果；携带 controller 发布终态事件所需的运行现场摘要。"""

    run_ref: HarnessRunRef
    status: HarnessStatus
    final_answer: str = Field(min_length=1, max_length=20_000)
    iteration: int = Field(default=0, ge=0)
    last_error: RunError | None = None

    @model_validator(mode="after")
    def validate_terminal_result(self) -> "FinalizationResult":
        if self.status not in {
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
            HarnessStatus.TIMEOUT,
        }:
            raise ValueError("FinalizationResult 必须表示 Harness 终态")
        return self


class LoopRunResult(ContractModel):
    """LoopController.start() 在 A 阶段返回的唯一结果。"""

    run_ref: HarnessRunRef
    status: HarnessStatus
    phase: LoopPhase
    iteration: int = Field(ge=0)
    finalization_result: FinalizationResult
    last_error: RunError | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "LoopRunResult":
        if self.phase is not LoopPhase.FINALIZATION:
            raise ValueError("终态结果的 phase 必须为 finalization")
        if self.finalization_result.run_ref != self.run_ref:
            raise ValueError("LoopRunResult 与 FinalizationResult 的 run_ref 必须一致")
        if self.finalization_result.status is not self.status:
            raise ValueError("LoopRunResult 与 FinalizationResult 的 status 必须一致")
        return self


class LoopPausedResult(ContractModel):
    """D 阶段返回给前端的持久化等待结果。"""

    run_ref: HarnessRunRef
    status: HarnessStatus = HarnessStatus.WAITING_CONFIRMATION
    phase: LoopPhase = LoopPhase.WAIT_CONFIRMATION
    iteration: int = Field(ge=0)
    confirmation: ConfirmationRequest
    state_version: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_paused_result(self) -> "LoopPausedResult":
        if self.status is not HarnessStatus.WAITING_CONFIRMATION:
            raise ValueError("暂停结果的 status 必须为 waiting_confirmation")
        if self.phase is not LoopPhase.WAIT_CONFIRMATION:
            raise ValueError("暂停结果的 phase 必须为 wait_confirmation")
        return self


class LoopResumeAcceptedResult(ContractModel):
    """重复确认请求的受控响应；表示恢复已被之前的请求消费。"""

    run_ref: HarnessRunRef
    resume_status: Literal["accepted", "idempotent"]
    status: HarnessStatus = HarnessStatus.RUNNING
    phase: LoopPhase = LoopPhase.RESTORE_RUN
    state_version: int = Field(ge=0)


class ResumeRunCommand(ContractModel):
    """恢复已有 Harness run 的用户确认命令。"""

    run_ref: HarnessRunRef
    reply: ConfirmationReply


LoopResult = LoopRunResult | LoopPausedResult | LoopResumeAcceptedResult


class ContextBuilder(Protocol):
    """ContextEngine 的最小异步调用边界。"""

    async def build(self, request: ContextRequest) -> CompiledContext: ...


class PlanningPort(Protocol):
    """Planner 输入和候选动作的调用边界。"""

    async def plan(
        self,
        value: PlannerInput,
        *,
        issuance: ActionIssuanceContext,
    ) -> NextAction: ...

class ConfirmationDispatcher(Protocol):
    """切片 B 的即时确认边界；切片 D 替换为暂停恢复实现。"""

    async def dispatch(self, value: NextAction) -> str: ...

class ToolRuntimePort(ToolRuntime, Protocol):
    """只接收已提交动作的工具执行边界。"""

    async def execute(self, request: ToolExecutionRequest): ...


class FinalizationPort(Protocol):
    """最终收口的调用边界；A 使用 Fake，E 替换为 M6。"""

    async def finalize(self, value: FinalizationInput) -> FinalizationResult: ...


class HarnessRunStore(Protocol):
    """Harness 运行现场和确认恢复的持久化边界。"""

    async def create(self, run_ref: HarnessRunRef, state: HarnessGraphState) -> None: ...

    async def save(self, run_ref: HarnessRunRef, state: HarnessGraphState) -> None: ...

    async def load(self, run_ref: HarnessRunRef) -> HarnessGraphState: ...

    async def load_by_id(self, *, run_id: str, user_id: str) -> HarnessGraphState: ...

    async def pause_for_confirmation(
        self,
        run_ref: HarnessRunRef,
        state: HarnessGraphState,
        record: ConfirmationRecord,
    ) -> None: ...

    async def resolve_confirmation(
        self,
        run_ref: HarnessRunRef,
        reply: ConfirmationReply,
    ) -> ConfirmationResolution: ...


__all__ = [
    "ContextBuilder",
    "FinalizationInput",
    "FinalizationPort",
    "FinalizationResult",
    "HarnessRunStore",
    "LoopPausedResult",
    "LoopResumeAcceptedResult",
    "LoopResult",
    "LoopRunResult",
    "PlanningPort",
    "ConfirmationDispatcher",
    "ToolRuntimePort",
    "StartRunCommand",
    "ResumeRunCommand",
]
