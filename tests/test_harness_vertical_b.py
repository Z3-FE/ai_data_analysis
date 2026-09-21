"""切片 B：真实 PlanningAgent 加动作提交和三类动作分派的纵向验收。"""

import unittest

from app.agent.context_engine.contracts import ContextRequest
from app.agent.loop_controller.contracts import StartRunCommand
from app.agent.loop_controller.controller import LoopController
from app.agent.planning_agent.agent import PlanningAgent
from app.agent.state_result_store.contracts import (
    ActionType,
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
    PlannerCapabilities,
    ToolSpec,
)
from app.agent.streaming.writer import NullHarnessEventWriter
from tests.fakes.slice_a.fake_finalization import FakeFinalizationService
from tests.fakes.slice_a.fake_run_store import FakeRunStore
from tests.fakes.slice_b.fake_action_committer import FakeActionCommitter
from tests.fakes.slice_b.fake_confirmation_dispatcher import FakeConfirmationDispatcher
from tests.fakes.slice_b.fake_tool_runtime import FakeToolRuntime
from tests.test_context_engine import FakeMemoryReader, _engine


class ScriptedPlannerClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)

    async def complete(self, prompt: str) -> str:
        if not self.responses:
            raise AssertionError("测试 Planner 没有预置下一次响应")
        return self.responses.pop(0)


class SliceBVerticalTest(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, action_json: str, *follow_up_responses: str):
        context_engine, _, _ = _engine(FakeMemoryReader())
        action_committer = FakeActionCommitter()
        tool_runtime = FakeToolRuntime()
        confirmation_dispatcher = FakeConfirmationDispatcher()
        run_store = FakeRunStore()
        finalization_service = FakeFinalizationService(run_store)
        planning_agent = PlanningAgent(
            llm_client=ScriptedPlannerClient(action_json, *follow_up_responses),
            capabilities=PlannerCapabilities(allow_ask_user=True),
        )
        tool_specs = (
            ToolSpec(
                name="query_data",
                description="查询数据",
                permission="data.read",
                input_schema={"type": "object"},
            ),
        )
        run_ref = HarnessRunRef(
            user_id="user-1",
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            run_id="run-b",
        )
        controller = LoopController(
            context_builder=context_engine,
            planning_agent=planning_agent,
            finalization_service=finalization_service,
            run_store=run_store,
            action_committer=action_committer,
            tool_runtime=tool_runtime,
            confirmation_dispatcher=confirmation_dispatcher,
            tool_specs=tool_specs,
            event_writer=NullHarnessEventWriter(run_ref=run_ref),
        )
        result = await controller.start(
            StartRunCommand(run_ref=run_ref, input_text="分析销售数据")
        )
        return result, action_committer, tool_runtime, confirmation_dispatcher, finalization_service, run_store, planning_agent

    async def test_tool_call_commits_before_fake_runtime(self) -> None:
        result, committer, runtime, confirmation, finalization, store, planner = await self.run_case(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{}}}',
            '{"action_type":"final_answer","final_answer":"工具结果已处理"}',
        )
        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.phase, LoopPhase.FINALIZATION)
        self.assertEqual(store.states["run-b"]["harness"]["action_seq"], 2)
        self.assertEqual(
            committer.events,
            [
                ("prepared", 1), ("checkpoint", 1), ("committed", 1),
                ("prepared", 2), ("checkpoint", 2), ("committed", 2),
            ],
        )
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(runtime.calls[0].action_seq, 1)
        self.assertEqual(runtime.calls[0].tool_call.action_id, "run-b:i0:a1")
        self.assertEqual(len(confirmation.calls), 0)
        self.assertEqual(
            [snapshot["harness"]["phase"] for snapshot in store.snapshots],
            [
                "start_run", "build_context", "plan", "validate_action",
                "execute_tool", "handle_tool_result", "record_observation",
                "build_context", "plan", "validate_action",
                "finalization", "finalization",
            ],
        )
        self.assertEqual(planner.calls[0].tool_specs[0].name, "query_data")

    async def test_ask_user_is_dispatched_without_pause_state(self) -> None:
        result, committer, runtime, confirmation, finalization, store, _ = await self.run_case(
            '{"action_type":"ask_user","ask_user":{"question":"哪个月份？","reason_code":"missing_condition"}}'
        )
        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        self.assertEqual(len(committer.calls), 1)
        self.assertEqual(len(confirmation.calls), 1)
        self.assertEqual(len(runtime.calls), 0)
        self.assertEqual(result.finalization_result.final_answer, "用户确认完成。")
        self.assertEqual(
            [snapshot["harness"]["phase"] for snapshot in store.snapshots],
            ["start_run", "build_context", "plan", "validate_action", "finalization", "finalization"],
        )

    async def test_final_answer_is_also_committed(self) -> None:
        result, committer, runtime, confirmation, _, store, _ = await self.run_case(
            '{"action_type":"final_answer","final_answer":"直接完成"}'
        )
        self.assertEqual(result.finalization_result.final_answer, "直接完成")
        self.assertEqual(committer.calls[0].action.action_type, ActionType.FINAL_ANSWER)
        self.assertEqual(store.states["run-b"]["harness"]["action_seq"], 1)
        self.assertEqual(len(runtime.calls), 0)
        self.assertEqual(len(confirmation.calls), 0)


if __name__ == "__main__":
    unittest.main()
