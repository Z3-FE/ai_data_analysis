"""切片 B 动作提交边界。"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from app.agent.state_result_store.contracts import (
    ActionType,
    ContractModel,
    HarnessRunRef,
    NextAction,
    RunError,
    RunExecutionFence,
)


class ActionCommitRequest(ContractModel):
    """提交一个已经校验、但尚未成为正式动作的候选动作。"""

    run_ref: HarnessRunRef
    action: NextAction
    expected_action_seq: int = Field(ge=1)
    expected_state_version: int = Field(ge=0)
    expected_checkpoint_revision: int = Field(ge=0)
    execution_fence: RunExecutionFence

    @model_validator(mode="after")
    def validate_expected_seq(self) -> "ActionCommitRequest":
        if self.action.action_seq != self.expected_action_seq:
            raise ValueError("expected_action_seq 必须与 action.action_seq 一致")
        return self


class ActionCommitResult(ContractModel):
    """动作已经可见给下游的提交结果。"""

    status: Literal["committed", "idempotent"]
    action_seq: int = Field(ge=1)
    action_type: ActionType
    action_id: str | None = Field(default=None, min_length=1)


class ActionCommitFailure(Exception):
    """提交失败只携带统一运行错误。"""

    def __init__(self, error: RunError) -> None:
        self.error = error
        super().__init__(error.message)


class ActionCommitter(Protocol):
    async def commit(self, request: ActionCommitRequest) -> ActionCommitResult:
        """按 prepared -> checkpoint -> committed 顺序提交动作。"""


__all__ = [
    "ActionCommitFailure",
    "ActionCommitRequest",
    "ActionCommitResult",
    "ActionCommitter",
]
