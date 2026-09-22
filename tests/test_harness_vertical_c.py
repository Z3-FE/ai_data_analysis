"""切片 C：真实 ToolRuntime、QueryDataTool 和查询图边界的纵向验收。"""

import unittest
from unittest.mock import AsyncMock, patch

from app.agent.business_tools.query_data import QueryDataTool
from app.agent.loop_controller.contracts import StartRunCommand
from app.agent.loop_controller.controller import LoopController
from app.agent.planning_agent.agent import PlanningAgent
from app.agent.state_result_store.contracts import (
    HarnessRunRef,
    HarnessStatusType,
    LoopPhaseStatusType,
    PlannerCapabilities,
    ToolSpec,
)
from app.agent.streaming.writer import NullHarnessEventWriter
from app.agent.tool_runtime.registry import ToolRegistry
from app.agent.tool_runtime.runtime import ToolRuntime
from tests.fakes.slice_a.fake_finalization import FakeFinalizationService
from tests.fakes.slice_a.fake_run_store import FakeRunStore
from tests.fakes.slice_b.fake_action_committer import FakeActionCommitter
from tests.test_context_engine import FakeMemoryReader, _asset, _engine


class ScriptedPlannerClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class SliceCVerticalTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_observation_rebuild_context_and_final_answer(self) -> None:
        context_engine, _, _ = _engine(
            FakeMemoryReader(assets={"asset-1": _asset("asset-1")})
        )
        planner = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
                '{"action_type":"final_answer","final_answer":"销售额查询完成"}',
            ),
            capabilities=PlannerCapabilities(),
        )
        run_ref = HarnessRunRef(
            user_id="user-1",
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            run_id="run-c",
        )
        graph_calls: list[dict] = []

        async def query_graph_call(state, *, context):
            graph_calls.append({"state": state, "context": context})
            return {
                "sql": "SELECT amount FROM sales",
                "sql_result": [{"amount": 120}],
                "display_sql_result": [{"amount": 120}],
                "result_columns": [{"result_name": "amount"}],
                "mapping_limitations": [],
            }

        tool = QueryDataTool(
            context={"llm_client": object(), "dw_repository": object()},
            run_ref=run_ref,
            asset_ids=("asset-1",),
        )
        spec = ToolSpec(
            name="query_data",
            description="查询数据",
            permission="data.query.read",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        runtime = ToolRuntime(ToolRegistry({"query_data": (spec, tool)}))
        store = FakeRunStore()
        finalization = FakeFinalizationService(store)
        committer = FakeActionCommitter()
        controller = LoopController(
            context_engine=context_engine,
            planning_agent=planner,
            finalization_service=finalization,
            run_store=store,
            action_committer=committer,
            tool_runtime=runtime,
            tool_specs=(spec,),
            event_writer=NullHarnessEventWriter(run_ref=run_ref),
        )

        async def query_graph_stream(state, *, context, stream_mode):
            self.assertEqual(stream_mode, ["custom", "values"])
            result = await query_graph_call(state, context=context)
            yield ("custom", {"type": "progress", "node": "execute_sql", "status": "success"})
            yield ("values", result)

        with patch("app.agent.business_tools.query_data.tool.query_graph") as graph:
            graph.astream = query_graph_stream
            result = await controller.start(
                StartRunCommand(
                    run_ref=run_ref,
                    input_text="分析销售数据",
                    asset_ids=("asset-1",),
                )
            )

        self.assertEqual(result.status, HarnessStatusType.COMPLETED)
        self.assertEqual(result.phase, LoopPhaseStatusType.FINALIZATION)
        self.assertEqual(result.finalization_result.final_answer, "销售额查询完成")
        self.assertEqual([call.action.action_seq for call in committer.calls], [1, 2])
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(len(graph_calls), 1)
        self.assertEqual(graph_calls[0]["state"]["user_id"], "user-1")
        self.assertEqual(graph_calls[0]["state"]["asset_ids"], ["asset-1"])
        self.assertEqual(len(planner.calls), 2)
        self.assertEqual(len(planner.calls[1].state_view.observations), 1)
        self.assertEqual(
            planner.calls[1].state_view.observations[0].status.value,
            "success",
        )
        self.assertIn("120", planner.calls[1].state_view.observations[0].summary)
        relevant_phases = [
            snapshot["harness"]["phase"]
            for snapshot in store.snapshots
            if snapshot["harness"]["phase"] in {
                "handle_tool_result",
                "record_observation",
                "build_context",
                "plan",
            }
        ]
        self.assertEqual(
            relevant_phases,
            ["build_context", "plan", "handle_tool_result", "record_observation", "build_context", "plan"],
        )
        final_state = store.states[run_ref.run_id]["harness"]
        self.assertEqual(final_state["action_seq"], 2)
        self.assertEqual(final_state["iteration"], 1)
        self.assertEqual(len(final_state["observations"]), 1)
        self.assertIn("amount", str(final_state["observations"]))
        self.assertNotIn("sql_result", str(final_state["observations"]))
        self.assertNotIn("'rows'", str(final_state["observations"]))


if __name__ == "__main__":
    unittest.main()
