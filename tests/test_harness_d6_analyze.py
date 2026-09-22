"""Harness D6：analyze_data 的真实分析节点边界和结果隔离。"""

import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from langchain_core.runnables import RunnableLambda

from app.agent.business_tools.analyze_data import AnalyzeDataTool
from app.agent.loop_controller.contracts import StartRunCommand
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


class ScriptedPlannerClient:
    """只为本测试提供两次 Planner 动作，不替换生产 PlanningAgent。"""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    async def complete(self, prompt: str) -> str:
        if not self.responses:
            raise AssertionError("Planner 没有预置下一次响应")
        return self.responses.pop(0)


class FakeArtifactStore:
    """保存完整分析产物，模拟生产 ArtifactStore 的跨模块契约。"""

    def __init__(self) -> None:
        self.records: dict[str, ArtifactRecord] = {}

    async def save(self, request: ArtifactWriteRequest) -> ArtifactRecord:
        now = datetime.now(UTC)
        record = ArtifactRecord(
            artifact_id="a" * 64,
            result_ref=f"artifact://{request.action_id}",
            run_ref=request.run_ref,
            action_id=request.action_id,
            tool_name=request.tool_name,
            artifact_kind=request.artifact_kind,
            payload=request.payload,
            payload_hash="b" * 64,
            size_bytes=len(json.dumps(request.payload, default=str)),
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
        self.records[record.result_ref] = record
        return record

    async def read(self, request: ArtifactReadRequest) -> ArtifactRecord:
        return self.records[request.result_ref]


class FakeQueryGraph:
    """返回真实分析节点可以消费的 SQL 结果事件。"""

    def __init__(self) -> None:
        self.states: list[dict] = []

    async def astream(self, state, context, stream_mode):
        self.states.append(state)
        yield (
            "values",
            {
                "sql": "SELECT month, sales FROM monthly_sales",
                "sql_result": [
                    {"month": "2026-01", "sales": 100},
                    {"month": "2026-02", "sales": 120},
                ],
                "result_columns": [
                    {"result_name": "month"},
                    {"result_name": "sales"},
                ],
                "display_sql_result": [
                    {"month": "2026-01", "sales": 100},
                    {"month": "2026-02", "sales": 120},
                ],
                "mapping_limitations": [],
            },
        )


class HarnessD6AnalyzeTest(unittest.IsolatedAsyncioTestCase):
    async def test_analyze_data_runs_real_nodes_and_rebuilds_context_from_summary(self) -> None:
        def analysis_llm(prompt):
            text = prompt.to_string() if hasattr(prompt, "to_string") else str(prompt)
            if "数据分析计算代码生成器" in text:
                return json.dumps(
                    {
                        "code": (
                            "def calculate(rows):\n"
                            "    return {\"total_sales\": sum(row[\"sales\"] for row in rows)}"
                        ),
                        "result_description": "返回全部月份的销售额合计",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "analysis_summary": "计算月度销售额合计",
                    "tasks": [
                        {
                            "task_id": "monthly_sales",
                            "question": "查询每月销售额",
                            "purpose": "计算销售额合计",
                            "depends_on": [],
                        }
                    ],
                },
                ensure_ascii=False,
            )

        tool_context = {
            "llm_client": RunnableLambda(analysis_llm),
            "llm_timeout_seconds": 5,
        }
        run_ref = HarnessRunRef(
            user_id="user-1",
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            run_id="run-d6",
        )
        analysis_tool = AnalyzeDataTool(context=tool_context, run_ref=run_ref)
        spec = ToolSpec(
            name="analyze_data",
            description="根据用户目标执行真实数据分析",
            permission="data.analysis.read",
            result_kind="artifact",
            artifact_kind="analysis_result",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "analysis_goals": {"type": "array"},
                },
                "additionalProperties": False,
            },
        )
        artifact_store = FakeArtifactStore()
        runtime = ToolRuntime(
            ToolRegistry({"analyze_data": (spec, analysis_tool)}),
            artifact_store=artifact_store,
        )
        context_engine, _, _ = _engine(FakeMemoryReader())
        planner = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                "{\"action_type\":\"tool_call\",\"tool_call\":{\"tool_name\":\"analyze_data\",\"arguments\":{\"query\":\"计算月度销售额合计\"}}}",
                "{\"action_type\":\"final_answer\",\"final_answer\":\"月度销售额合计为 220。\"}",
            ),
            capabilities=PlannerCapabilities(),
        )
        store = FakeRunStore()
        controller = LoopController(
            context_engine=context_engine,
            planning_agent=planner,
            finalization_service=FakeFinalizationService(store),
            run_store=store,
            action_committer=FakeActionCommitter(),
            tool_runtime=runtime,
            tool_specs=(spec,),
            event_writer=NullHarnessEventWriter(run_ref=run_ref),
        )
        query_graph = FakeQueryGraph()

        with patch(
            "app.agent.nodes.execute_analysis.query_graph", query_graph
        ), patch(
            "app.agent.nodes.execute_analysis.execute_python_calculation",
            new=AsyncMock(return_value={"total_sales": 220}),
        ):
            result = await controller.start(
                StartRunCommand(run_ref=run_ref, input_text="计算月度销售额合计")
            )

        self.assertEqual(result.status, HarnessStatusType.COMPLETED)
        self.assertEqual(result.finalization_result.final_answer, "月度销售额合计为 220。")
        self.assertEqual(len(query_graph.states), 1)
        self.assertEqual(query_graph.states[0]["run_id"], run_ref.run_id)
        self.assertEqual(len(artifact_store.records), 1)
        artifact = next(iter(artifact_store.records.values()))
        self.assertEqual(artifact.payload["analysis_task_results"][0]["rows"][0]["sales"], 100)
        self.assertIn("def calculate(rows):", artifact.payload["analysis_task_results"][0]["python_code"])
        observation = store.states[run_ref.run_id]["harness"]["observations"][0]
        self.assertEqual(observation["result_ref"], artifact.result_ref)
        self.assertNotIn("rows", observation)
        self.assertNotIn("python_code", observation)
        self.assertEqual(planner.calls[1].state_view.observations[0].result_ref, artifact.result_ref)
        self.assertIn("220", planner.calls[1].state_view.observations[0].summary)


if __name__ == "__main__":
    unittest.main()
