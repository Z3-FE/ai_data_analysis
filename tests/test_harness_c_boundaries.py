"""切片 C 的失败恢复、结果引用和查询读取边界。"""

import unittest
from datetime import UTC, datetime, timedelta

from pydantic import Field

from app.agent.loop_controller.contracts import StartRunCommand
from app.agent.loop_controller.controller import LoopController
from app.agent.planning_agent.contracts import ActionIssuanceContext
from app.agent.planning_agent.errors import planner_error
from app.agent.state_result_store.contracts import (
    ActionType,
    ContractModel,
    HarnessRunRef,
    HarnessStatus,
    NextAction,
    ResultStatus,
    ToolCall,
    ToolSpec,
)
from app.agent.tool_runtime.artifacts import (
    ArtifactReadRequest,
    ArtifactRecord,
    ArtifactStoreError,
    ArtifactWriteRequest,
)
from app.agent.tool_runtime.contracts import ToolExecutionRequest
from app.agent.tool_runtime.registry import ToolRegistry
from app.agent.tool_runtime.runtime import ToolRuntime
from app.repositories.dw_repository import DwRepository
from tests.fakes.slice_a.fake_finalization import FakeFinalizationService
from tests.fakes.slice_a.fake_run_store import FakeRunStore
from tests.test_context_engine import FakeMemoryReader, _engine


def _run_ref() -> HarnessRunRef:
    return HarnessRunRef(
        user_id="user-1",
        conversation_id="conversation-1",
        thread_id="thread-1",
        turn_id="turn-1",
        run_id="run-boundary",
    )


class RetryPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, value, *, issuance: ActionIssuanceContext) -> NextAction:
        self.calls += 1
        if self.calls == 1:
            raise planner_error(
                "invalid_output",
                "temporary planner parse failure",
                retryable=True,
            )
        return NextAction(
            action_seq=issuance.action_seq,
            action_type=ActionType.FINAL_ANSWER,
            final_answer="重试后完成",
        )


class PlannerRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_planner_failure_is_retried_by_loop_controller(self) -> None:
        context_engine, _, _ = _engine(FakeMemoryReader())
        planner = RetryPlanner()
        store = FakeRunStore()
        run_ref = _run_ref()
        controller = LoopController(
            context_builder=context_engine,
            planning_agent=planner,
            finalization_service=FakeFinalizationService(),
            run_store=store,
            max_planner_retries=2,
        )

        result = await controller.start(
            StartRunCommand(run_ref=run_ref, input_text="分析销售额")
        )

        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        self.assertEqual(planner.calls, 2)
        final_state = store.states[run_ref.run_id]["harness"]
        self.assertEqual(final_state["planner_retry_count"], 0)
        self.assertIsNone(final_state["last_error"])


class ArtifactInput(ContractModel):
    value: int = Field(ge=1)


class ArtifactOutput(ContractModel):
    result_ref: str | None = None
    limitations: list[str] = Field(default_factory=list)
    mapping_limitations: list[str] = Field(default_factory=list)


class ArtifactTool:
    name = "artifact_tool"
    input_model = ArtifactInput

    async def execute(self, value: ArtifactInput) -> ArtifactOutput:
        return ArtifactOutput(
            result_ref="tool-generated-ref",
            limitations=["query limited"],
            mapping_limitations=["mapping limited"],
        )


class FakeArtifactStore:
    def __init__(self) -> None:
        self.writes: list[ArtifactWriteRequest] = []
        self.records: dict[str, ArtifactRecord] = {}

    async def save(self, request: ArtifactWriteRequest) -> ArtifactRecord:
        self.writes.append(request)
        now = datetime.now(UTC)
        record = ArtifactRecord(
            artifact_id="a" * 64,
            result_ref="artifact://server-ref",
            run_ref=request.run_ref,
            action_id=request.action_id,
            tool_name=request.tool_name,
            artifact_kind=request.artifact_kind,
            payload=request.payload,
            payload_hash="b" * 64,
            size_bytes=1,
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
        self.records[record.result_ref] = record
        return record

    async def read(self, request: ArtifactReadRequest) -> ArtifactRecord:
        record = self.records.get(request.result_ref)
        if record is None or record.run_ref != request.run_ref:
            raise ArtifactStoreError(
                "artifact_not_found",
                "Artifact 不属于当前运行",
                retryable=False,
            )
        return record


class ArtifactBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_owns_reference_and_enforces_identity_on_read(self) -> None:
        store = FakeArtifactStore()
        spec = ToolSpec(
            name="artifact_tool",
            description="测试 Artifact",
            permission="data.read",
            result_kind="artifact",
            artifact_kind="analysis_result",
        )
        runtime = ToolRuntime(
            ToolRegistry({"artifact_tool": (spec, ArtifactTool())}),
            artifact_store=store,
        )
        request = ToolExecutionRequest(
            run_ref=_run_ref(),
            tool_call=ToolCall(
                action_id="run-boundary:i0:a1",
                tool_name="artifact_tool",
                arguments={"value": 1},
            ),
            action_seq=1,
        )

        result = await runtime.execute(request)

        self.assertEqual(result.status, ResultStatus.PARTIAL)
        self.assertEqual(result.result_ref, "artifact://server-ref")
        self.assertEqual(result.output_hash, "b" * 64)
        self.assertEqual(
            result.limitations, ["query limited", "mapping limited"]
        )
        self.assertEqual(store.writes[0].action_id, "run-boundary:i0:a1")
        with self.assertRaises(ArtifactStoreError):
            await runtime.read_result(
                ArtifactReadRequest(
                    run_ref=_run_ref().model_copy(update={"user_id": "other"}),
                    result_ref=result.result_ref,
                )
            )


class FakeMappingResult:
    def __init__(self, rows: list[dict[str, int]]) -> None:
        self.rows = rows
        self.closed = False

    def keys(self) -> list[str]:
        return ["id"]

    def mappings(self) -> "FakeMappingResult":
        return self

    async def fetchmany(self, size: int) -> list[dict[str, int]]:
        return self.rows[:size]

    async def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, result: FakeMappingResult) -> None:
        self.result = result

    async def stream(self, statement) -> FakeMappingResult:
        return self.result


class QueryBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_repository_enforces_row_limit_and_closes_stream(self) -> None:
        result = FakeMappingResult([
            {"id": 1},
            {"id": 2},
            {"id": 3},
        ])
        value = await DwRepository(FakeSession(result)).execute_query(
            "select id from sales", max_rows=2
        )

        self.assertEqual(value.rows, [{"id": 1}, {"id": 2}])
        self.assertTrue(value.truncated)
        self.assertEqual(value.column_names, ["id"])
        self.assertTrue(result.closed)
        with self.assertRaises(ValueError):
            await DwRepository(FakeSession(result)).execute_query("select 1", max_rows=0)
