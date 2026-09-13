"""Harness Tool Runtime 的最小真实实现。"""

from __future__ import annotations

from datetime import UTC, datetime
from time import monotonic

from app.agent.business_tools.query_data.contracts import QueryDataInput
from app.agent.state_result_store.contracts import (
    ErrorCategory,
    ResultStatus,
    ToolResult,
)
from app.agent.tool_runtime.contracts import ToolExecutionRequest
from app.agent.tool_runtime.registry import ToolRegistry


class ToolRuntime:
    """校验注册工具并把真实工具输出归一化为 M1 ToolResult。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry
        self.calls: list[ToolExecutionRequest] = []

    async def execute(self, request: ToolExecutionRequest) -> ToolResult:
        self.calls.append(request)
        started_at = datetime.now(UTC)
        started = monotonic()
        try:
            spec, tool = self.registry.get(request.tool_call.tool_name)
            if request.tool_call.tool_name == "query_data":
                output = await tool.execute(
                    QueryDataInput.model_validate(request.tool_call.arguments)
                )
            else:
                output = await tool.execute(request.tool_call.arguments)
            summary = (
                f"{spec.name} 执行成功，返回 {output.row_count} 行、"
                f"{output.column_count} 个字段。"
            )
            return ToolResult(
                tool_call_id=request.tool_call.action_id,
                tool_name=request.tool_call.tool_name,
                status=ResultStatus.SUCCESS,
                summary=summary,
                limitations=list(output.mapping_limitations),
                started_at=started_at,
                finished_at=datetime.now(UTC),
                duration_ms=max(0, int((monotonic() - started) * 1000)),
            )
        except Exception as exc:
            finished_at = datetime.now(UTC)
            return ToolResult(
                tool_call_id=request.tool_call.action_id,
                tool_name=request.tool_call.tool_name,
                status=ResultStatus.UNRECOVERABLE_ERROR,
                summary=f"{request.tool_call.tool_name} 执行失败。",
                error_category=ErrorCategory.TOOL,
                error_code="tool_execution_failed",
                error_message=str(exc),
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(0, int((monotonic() - started) * 1000)),
            )


__all__ = ["ToolRuntime"]
