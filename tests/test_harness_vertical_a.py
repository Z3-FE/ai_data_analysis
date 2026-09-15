"""切片 A：从 LoopController.start() 到完成结果的纵向验收。"""

import unittest

from app.agent.state_result_store.contracts import HarnessRunRef, HarnessStatus, LoopPhase
from app.agent.loop_controller.contracts import StartRunCommand
from app.agent.loop_controller.controller import LoopController
from tests.fakes.slice_a.fake_finalization import FakeFinalizationService
from tests.fakes.slice_a.fake_planning_agent import FakePlanningAgent
from tests.fakes.slice_a.fake_run_store import FakeRunStore
from tests.test_context_engine import FakeMemoryReader, _engine


class SliceAVerticalTest(unittest.IsolatedAsyncioTestCase):
    async def test_slice_a_start_to_result(self) -> None:
        context_engine, _, _ = _engine(FakeMemoryReader())
        planning_agent = FakePlanningAgent(final_answer="销售数据分析已完成。")
        finalization_service = FakeFinalizationService()
        run_store = FakeRunStore()
        run_ref = HarnessRunRef(
            user_id="user-1",
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            run_id="run-1",
        )
        controller = LoopController(
            context_builder=context_engine,
            planning_agent=planning_agent,
            finalization_service=finalization_service,
            run_store=run_store,
        )

        result = await controller.start(
            StartRunCommand(run_ref=run_ref, input_text="分析销售数据")
        )

        self.assertEqual(result.run_ref, run_ref)
        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.phase, LoopPhase.FINALIZATION)
        self.assertEqual(result.finalization_result.final_answer, "销售数据分析已完成。")
        self.assertEqual(len(planning_agent.calls), 1)
        self.assertEqual(len(finalization_service.calls), 1)
        self.assertEqual(
            finalization_service.calls[0].compiled_context.build_id,
            planning_agent.calls[0].compiled_context.build_id,
        )
        self.assertEqual(
            [snapshot["harness"]["phase"] for snapshot in run_store.snapshots],
            [
                "start_run", "build_context", "plan",
                "validate_action", "finalization", "finalization",
            ],
        )
        final_state = run_store.states[run_ref.run_id]["harness"]
        self.assertEqual(final_state["status"], "completed")
        self.assertEqual(final_state["phase"], "finalization")
        self.assertEqual(final_state["action_seq"], 0)
        self.assertEqual(final_state["iteration"], 0)


if __name__ == "__main__":
    unittest.main()
