"""切片 A 的最小运行编排契约。

这些 DTO 只覆盖从 start 到 completed 的纵向闭环。后续切片可以复用
``PlannerInput``、``NextAction`` 和 ``CompiledContext``，再将收口 DTO
迁移到正式的 M6 模块，而不改变 LoopController 的依赖边界。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from pydantic import Field, model_validator

from app.agent.context_engine.contracts import CompiledContext, ContextRequest
from app.agent.harness.contracts import (
    ContractModel,
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
    NextAction,
    PlannerInput,
    RunError,
)

if TYPE_CHECKING:
    from app.agent.state import HarnessGraphState


class StartRunCommand(ContractModel):
    """创建一个新的 Harness run；身份由调用方提供。"""

    run_ref: HarnessRunRef
    input_text: str = Field(min_length=1, max_length=20_000)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)


class FinalizationInput(ContractModel):
    """A 阶段交给假收口的受控输入。"""

    run_ref: HarnessRunRef
    user_query: str = Field(min_length=1, max_length=20_000)
    compiled_context: CompiledContext
    final_answer: str = Field(min_length=1, max_length=20_000)


class FinalizationResult(ContractModel):
    """A 阶段收口结果；正式 M6 将扩展其持久化和审计字段。"""

    run_ref: HarnessRunRef
    status: HarnessStatus
    final_answer: str = Field(min_length=1, max_length=20_000)

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


class ContextBuilder(Protocol):
    """ContextEngine 的最小异步调用边界。"""

    async def build(self, request: ContextRequest) -> CompiledContext: ...


class PlanningPort(Protocol):
    """Planner 输入和候选动作的调用边界。"""

    async def plan(self, value: PlannerInput) -> NextAction: ...


class FinalizationPort(Protocol):
    """最终收口的调用边界；A 使用 Fake，E 替换为 M6。"""

    async def finalize(self, value: FinalizationInput) -> FinalizationResult: ...


class HarnessRunStore(Protocol):
    """A 阶段最小状态保存边界；不代表生产 PostgreSQL RunStore。"""

    async def create(self, run_ref: HarnessRunRef, state: HarnessGraphState) -> None: ...

    async def save(self, run_ref: HarnessRunRef, state: HarnessGraphState) -> None: ...

    async def load(self, run_ref: HarnessRunRef) -> HarnessGraphState: ...


__all__ = [
    "ContextBuilder",
    "FinalizationInput",
    "FinalizationPort",
    "FinalizationResult",
    "HarnessRunStore",
    "LoopRunResult",
    "PlanningPort",
    "StartRunCommand",
]
