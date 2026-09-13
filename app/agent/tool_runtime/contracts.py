"""切片 B Tool Runtime 协议；真实工具实现留给切片 C。"""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from app.agent.state_result_store.contracts import (
    ContractModel,
    HarnessRunRef,
    ToolCall,
    ToolResult,
)


class ToolExecutionRequest(ContractModel):
    """只允许执行已提交动作的工具请求。"""

    run_ref: HarnessRunRef
    tool_call: ToolCall
    action_seq: int = Field(ge=1)
    attempt: int = Field(default=1, ge=1)


class ToolRuntime(Protocol):
    async def execute(self, request: ToolExecutionRequest) -> ToolResult: ...


__all__ = ["ToolExecutionRequest", "ToolRuntime"]
