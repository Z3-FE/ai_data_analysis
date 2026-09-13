"""切片 B 的工具替身。

只用于切片 B 端到端验收；切片 C 在 LoopController 的 tool_runtime
依赖注入点替换为 app.agent.tool_runtime.runtime.ToolRuntime。该替身不连接数据库。
"""

from datetime import UTC, datetime

from app.agent.state_result_store.contracts import ResultStatus, ToolResult
from app.agent.tool_runtime.contracts import ToolExecutionRequest


class FakeToolRuntime:
    """收到已提交 tool_call 后返回固定成功结果。"""

    def __init__(self) -> None:
        self.calls: list[ToolExecutionRequest] = []

    async def execute(self, request: ToolExecutionRequest) -> ToolResult:
        self.calls.append(request)
        now = datetime.now(UTC)
        return ToolResult(
            tool_call_id=request.tool_call.action_id,
            tool_name=request.tool_call.tool_name,
            status=ResultStatus.SUCCESS,
            summary=f"Fake tool executed: {request.tool_call.tool_name}",
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )


__all__ = ["FakeToolRuntime"]
