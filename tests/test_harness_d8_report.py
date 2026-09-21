"""Harness D8：build_report 的报告边界、结果隔离和结构化收口。"""

import json
import unittest
from datetime import UTC, datetime, timedelta

from langchain_core.runnables import RunnableLambda

from app.agent.business_tools.build_report import BuildReportInput, BuildReportTool
from app.agent.loop_controller.contracts import FinalizationInput, StartRunCommand
from app.agent.loop_controller.controller import LoopController
from app.agent.planning_agent.agent import PlanningAgent
from app.agent.state_result_store.contracts import (
    HarnessRunRef,
    HarnessStatusType,
    PlannerCapabilities,
    ToolSpec,
)
from app.agent.streaming.writer import NullHarnessEventWriter
from app.agent.tool_runtime.artifacts import (
    ArtifactReadRequest,
    ArtifactRecord,
    ArtifactWriteRequest,
)
from app.agent.tool_runtime.registry import ToolRegistry
from app.agent.tool_runtime.runtime import ToolRuntime
from tests.fakes.slice_a.fake_finalization import FakeFinalizationService
from tests.fakes.slice_a.fake_run_store import FakeRunStore
from tests.fakes.slice_b.fake_action_committer import FakeActionCommitter
from tests.test_context_engine import FakeMemoryReader, _engine

RUN_REF = HarnessRunRef(
    user_id="user-1",
    conversation_id="conversation-1",
    thread_id="thread-1",
    turn_id="turn-1",
    run_id="run-d8",
)
QUERY_PAYLOAD = {
    "row_count": 2,
    "column_count": 2,
    "summary": "query_data 查询完成，返回 2 行、2 个字段。",
    "sql": "SELECT month, sales FROM monthly_sales",
    "rows": [
        {"month": "2026-01", "sales": 100},
        {"month": "2026-02", "sales": 120},
    ],
    "display_rows": [
        {"month": "2026-01", "sales": 100},
        {"month": "2026-02", "sales": 120},
    ],
    "result_columns": [
        {"result_name": "month", "display_name": "月份", "field_role": "dimension"},
        {"result_name": "sales", "display_name": "销售额", "field_role": "metric"},
    ],
    "limitations": [],
    "mapping_limitations": [],
}
REPORT_PLAN = {
    "title": "月度销售额报告",
    "summary": "2026 年前两个月销售额小幅上升。",
    "sections": [
        {
            "title": "销售额趋势",
            "components": [
                {
                    "component_type": "chart",
                    "chart_type": "line",
                    "title": "月度销售额",
                    "data_ref": {
                        "source_task_id": "query",
                        "source": "rows",
                        "dimension_field": "month",
                        "metric_fields": ["sales"],
                    },
                }
            ],
        }
    ],
    "limitations": [],
}


class ScriptedPlannerClient:
    """只为本测试提供预置 Planner 动作，不替换生产 PlanningAgent。"""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    async def complete(self, prompt: str) -> str:
        if not self.responses:
            raise AssertionError("Planner 没有预置下一次响应")
        return self.responses.pop(0)


class FakeArtifactStore:
    """保存完整产物，模拟生产 ArtifactStore 的跨模块契约。"""

    def __init__(self) -> None:
        self.records: dict[str, ArtifactRecord] = {}

    def seed(self, *, result_ref: str, tool_name: str, artifact_kind: str, payload: dict):
        """预置上游数据工具已经产生的结果。"""
        now = datetime.now(UTC)
        record = ArtifactRecord(
            artifact_id="c" * 64,
            result_ref=result_ref,
            run_ref=RUN_REF,
            action_id=f"{result_ref}:action",
            tool_name=tool_name,
            artifact_kind=artifact_kind,
            payload=payload,
            payload_hash="d" * 64,
            size_bytes=len(json.dumps(payload, default=str)),
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
        self.records[result_ref] = record
        return record

    async def save(self, request: ArtifactWriteRequest) -> ArtifactRecord:
        return self.seed(
            result_ref=f"artifact://{request.action_id}",
            tool_name=request.tool_name,
            artifact_kind=request.artifact_kind,
            payload=request.payload,
        )

    async def read(self, request: ArtifactReadRequest) -> ArtifactRecord:
        record = self.records[request.result_ref]
        if record.run_ref != request.run_ref:
            raise AssertionError("Artifact 读取必须使用完整运行身份")
        return record


class FakeConversationRepository:
    """只记录 finish_turn 的受控输出，不做真实持久化。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def finish_turn(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        return True


def _report_spec() -> ToolSpec:
    return ToolSpec(
        name="build_report",
        description="把已完成的数据结果渲染为最终可视化报告。",
        permission="data.report.write",
        result_kind="report",
        artifact_kind="rendered_report",
        idempotency="conditionally_idempotent",
        retry_on_timeout=False,
        input_schema={
            "type": "object",
            "required": ["goal", "result_refs"],
            "properties": {
                "goal": {"type": "string"},
                "result_refs": {"type": "array", "minItems": 1},
            },
            "additionalProperties": False,
        },
    )


def _plan_llm(prompt) -> str:
    """报告规划节点唯一的 LLM 调用；只返回规划意图。"""
    return json.dumps(REPORT_PLAN, ensure_ascii=False)


class HarnessD8ReportTest(unittest.IsolatedAsyncioTestCase):
    async def test_build_report_binds_upstream_artifact_and_isolates_result(self) -> None:
        artifact_store = FakeArtifactStore()
        artifact_store.seed(
            result_ref="artifact://query-1",
            tool_name="query_data",
            artifact_kind="query_result",
            payload=QUERY_PAYLOAD,
        )
        spec = _report_spec()
        tool = BuildReportTool(
            context={"llm_client": RunnableLambda(_plan_llm), "llm_timeout_seconds": 5},
            run_ref=RUN_REF,
            artifact_store=artifact_store,
        )
        runtime = ToolRuntime(
            ToolRegistry({"build_report": (spec, tool)}),
            artifact_store=artifact_store,
        )
        context_engine, _, _ = _engine(FakeMemoryReader())
        planner = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                json.dumps(
                    {
                        "action_type": "tool_call",
                        "tool_call": {
                            "tool_name": "build_report",
                            "arguments": {
                                "goal": "生成月度销售额报告",
                                "result_refs": ["artifact://query-1"],
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"action_type": "final_answer", "final_answer": "报告已生成。"},
                    ensure_ascii=False,
                ),
            ),
            capabilities=PlannerCapabilities(),
        )
        store = FakeRunStore()
        finalization = FakeFinalizationService(store)
        controller = LoopController(
            context_builder=context_engine,
            planning_agent=planner,
            finalization_service=finalization,
            run_store=store,
            action_committer=FakeActionCommitter(),
            tool_runtime=runtime,
            tool_specs=(spec,),
            event_writer=NullHarnessEventWriter(run_ref=RUN_REF),
        )

        result = await controller.start(
            StartRunCommand(run_ref=RUN_REF, input_text="生成月度销售额报告")
        )

        self.assertEqual(result.status, HarnessStatusType.COMPLETED)
        report_artifact = next(
            record
            for record in artifact_store.records.values()
            if record.artifact_kind == "rendered_report"
        )
        report = report_artifact.payload["rendered_report"]
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["title"], "月度销售额报告")
        component = report["sections"][0]["components"][0]
        self.assertEqual(component["binding_status"], "bound")
        self.assertEqual(component["source_task_id"], "query")
        self.assertEqual(component["data"][0]["sales"], 100)

        observation = store.states[RUN_REF.run_id]["harness"]["observations"][0]
        self.assertEqual(observation["result_ref"], report_artifact.result_ref)
        self.assertNotIn("data", observation["summary"])
        self.assertNotIn("2026-01", observation["summary"])
        self.assertIn("月度销售额报告", observation["summary"])

        # 收口只携带引用，报告本体仍留在 Artifact。
        self.assertEqual(finalization.calls[-1].final_output_type, "rendered_report")
        self.assertEqual(
            finalization.calls[-1].final_output_ref, report_artifact.result_ref
        )

    async def test_build_report_rejects_unknown_upstream_result(self) -> None:
        artifact_store = FakeArtifactStore()
        artifact_store.seed(
            result_ref="artifact://report-1",
            tool_name="build_report",
            artifact_kind="rendered_report",
            payload={"rendered_report": {}},
        )
        tool = BuildReportTool(
            context={"llm_client": RunnableLambda(_plan_llm), "llm_timeout_seconds": 5},
            run_ref=RUN_REF,
            artifact_store=artifact_store,
        )
        with self.assertRaises(Exception):
            await tool.execute(
                BuildReportInput(
                    goal="生成报告", result_refs=["artifact://report-1"]
                )
            )

    async def test_finalization_saves_report_output_by_reference(self) -> None:
        from app.agent.finalization.service import PostgresFinalizationService

        artifact_store = FakeArtifactStore()
        artifact_store.seed(
            result_ref="artifact://report-1",
            tool_name="build_report",
            artifact_kind="rendered_report",
            payload={"rendered_report": {"status": "success", "title": "月度销售额报告"}},
        )
        conversations = FakeConversationRepository()
        service = PostgresFinalizationService(
            conversation_repository=conversations,
            ledger=None,
            run_store=FakeRunStore(),
            artifact_store=artifact_store,
        )

        await service._save_history(
            FinalizationInput(
                run_ref=RUN_REF,
                user_query="生成月度销售额报告",
                final_answer="报告已生成。",
                final_output_type="rendered_report",
                final_output_ref="artifact://report-1",
            )
        )
        self.assertEqual(conversations.calls[-1]["output_type"], "rendered_report")
        self.assertEqual(
            conversations.calls[-1]["output_payload"]["title"], "月度销售额报告"
        )

        await service._save_history(
            FinalizationInput(
                run_ref=RUN_REF,
                user_query="今天天气怎么样",
                final_answer="已记录。",
            )
        )
        self.assertEqual(conversations.calls[-1]["output_type"], "text")
        self.assertEqual(
            conversations.calls[-1]["output_payload"], {"message": "已记录。"}
        )


if __name__ == "__main__":
    unittest.main()
