"""ToolRuntime 的通用输入、结果和错误归一化测试。"""

import unittest

from pydantic import Field

from app.agent.state_result_store.contracts import (
    ContractModel,
    HarnessRunRef,
    ResultStatus,
    ToolCall,
    ToolSpec,
)
from app.agent.tool_runtime.contracts import ToolExecutionRequest
from app.agent.tool_runtime.registry import ToolRegistry
from app.agent.tool_runtime.runtime import ToolRuntime


class ExampleInput(ContractModel):
    value: int = Field(ge=1)


class ExampleOutput(ContractModel):
    result_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ExampleTool:
    name = "analyze_data"
    input_model = ExampleInput

    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[ExampleInput] = []

    async def execute(self, value: ExampleInput) -> ExampleOutput:
        self.calls.append(value)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def request(arguments: dict | None = None) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        run_ref=HarnessRunRef(
            user_id="user-1",
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            run_id="run-1",
        ),
        tool_call=ToolCall(
            action_id="run-1:i0:a1",
            tool_name="analyze_data",
            arguments=arguments or {"value": 1},
        ),
        action_seq=1,
    )


def runtime(tool: ExampleTool) -> ToolRuntime:
    spec = ToolSpec(
        name=tool.name,
        description="分析数据",
        permission="data.analysis.read",
        input_schema={"type": "object"},
    )
    return ToolRuntime(ToolRegistry({tool.name: (spec, tool)}))


class ToolRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_non_query_tool_uses_its_input_model_and_maps_refs(self) -> None:
        tool = ExampleTool(
            ExampleOutput(
                result_ref="artifact://result-1",
                evidence_refs=["evidence://source-1"],
                limitations=["部分数据缺失"],
            )
        )

        result = await runtime(tool).execute(request({"value": 2}))

        self.assertEqual(result.status, ResultStatus.PARTIAL)
        # 工具不能自行发布结果引用；统一引用由 ArtifactStore 生成。
        self.assertIsNone(result.result_ref)
        self.assertEqual(result.evidence_refs, ["evidence://source-1"])
        self.assertEqual(result.limitations, ["部分数据缺失"])
        self.assertEqual(tool.calls[0].value, 2)

    async def test_invalid_input_does_not_call_tool(self) -> None:
        tool = ExampleTool(ExampleOutput())

        result = await runtime(tool).execute(request({"value": 0}))

        self.assertEqual(result.status, ResultStatus.UNRECOVERABLE_ERROR)
        self.assertEqual(result.error_code, "invalid_tool_input")
        self.assertFalse(result.retryable)
        self.assertEqual(tool.calls, [])

    async def test_timeout_is_retryable(self) -> None:
        result = await runtime(ExampleTool(TimeoutError("too slow"))).execute(request())

        self.assertEqual(result.status, ResultStatus.TEMPORARY_ERROR)
        self.assertEqual(result.error_code, "tool_timeout")
        self.assertTrue(result.retryable)

    async def test_connection_error_is_retryable(self) -> None:
        result = await runtime(ExampleTool(ConnectionError("offline"))).execute(request())

        self.assertEqual(result.status, ResultStatus.TEMPORARY_ERROR)
        self.assertEqual(result.error_code, "tool_dependency_unavailable")
        self.assertTrue(result.retryable)

    async def test_unexpected_error_is_not_retryable(self) -> None:
        result = await runtime(ExampleTool(RuntimeError("broken"))).execute(request())

        self.assertEqual(result.status, ResultStatus.UNRECOVERABLE_ERROR)
        self.assertEqual(result.error_code, "tool_execution_failed")
        self.assertFalse(result.retryable)

    async def test_tool_value_error_is_not_misclassified_as_invalid_input(self) -> None:
        result = await runtime(ExampleTool(ValueError("business failure"))).execute(
            request()
        )

        self.assertEqual(result.status, ResultStatus.UNRECOVERABLE_ERROR)
        self.assertEqual(result.error_code, "tool_execution_failed")
        self.assertFalse(result.retryable)

    async def test_registry_rejects_mismatched_names(self) -> None:
        tool = ExampleTool(ExampleOutput())
        spec = ToolSpec(
            name="query_data",
            description="查询数据",
            permission="data.query.read",
        )

        with self.assertRaises(ValueError):
            ToolRegistry({tool.name: (spec, tool)})


if __name__ == "__main__":
    unittest.main()
