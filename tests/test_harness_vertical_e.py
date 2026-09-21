"""切片 E：真实 Finalization 与 Memory Formation 的纵向验收。

验收标准：从 LoopController.start() 开始，真实 PostgresFinalizationService
按 Ledger 固定顺序完成历史落库、终态 checkpoint、active_run_id 条件释放和
真实 MemoryFormationService.submit()，重复对账保持幂等。
"""

import unittest

from sqlalchemy import select

from app.agent.finalization.service import PostgresFinalizationService
from app.agent.loop_controller.contracts import StartRunCommand
from app.agent.loop_controller.controller import LoopController
from app.agent.state_result_store.contracts import HarnessStatusType
from app.agent.streaming.writer import NullHarnessEventWriter
from app.models.memory import MemoryFormationRunModel
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.finalization_repository import PostgresFinalizationLedger
from app.repositories.harness_run_repository import PostgresHarnessRunStore
from tests.fakes.slice_a.fake_planning_agent import FakePlanningAgent
from tests.harness_postgres import (
    build_formation_service,
    cleanup_user,
    create_session_factory,
    ensure_tables,
    new_run_identity,
)
from tests.test_context_engine import FakeMemoryReader, _engine

FINAL_ANSWER = "销售分析完成，10 月销售额合计 1,000 元。"


class SliceEVerticalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine, self.session_factory = create_session_factory()
        await ensure_tables(self.engine)
        self.identity = new_run_identity("e")

    async def asyncTearDown(self) -> None:
        await cleanup_user(self.session_factory, self.identity["user_id"])
        await self.engine.dispose()

    def _run_ref(self):
        from app.agent.state_result_store.contracts import HarnessRunRef

        return HarnessRunRef(**self.identity)

    def _controller(self) -> LoopController:
        run_store = PostgresHarnessRunStore(self.session_factory)
        finalization = PostgresFinalizationService(
            conversation_repository=ConversationRepository(self.session_factory),
            ledger=PostgresFinalizationLedger(self.session_factory),
            run_store=run_store,
            memory_formation_service=build_formation_service(self.session_factory),
        )
        context_engine, _, _ = _engine(FakeMemoryReader())
        return LoopController(
            context_builder=context_engine,
            planning_agent=FakePlanningAgent(final_answer=FINAL_ANSWER),
            finalization_service=finalization,
            run_store=run_store,
            event_writer=NullHarnessEventWriter(run_ref=self._run_ref()),
        )

    async def _start_turn(self) -> None:
        repository = ConversationRepository(self.session_factory)
        await repository.ensure_conversation(
            self.identity["conversation_id"], self.identity["user_id"]
        )
        await repository.start_turn(
            conversation_id=self.identity["conversation_id"],
            user_id=self.identity["user_id"],
            thread_id=self.identity["thread_id"],
            turn_id=self.identity["turn_id"],
            run_id=self.identity["run_id"],
            input_text="分析上月销售趋势",
        )

    async def _formation_runs(self) -> list[MemoryFormationRunModel]:
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(MemoryFormationRunModel).where(
                            MemoryFormationRunModel.user_id == self.identity["user_id"]
                        )
                    )
                ).all()
            )

    async def test_slice_e_start_to_result_finalizes_history_checkpoint_and_memory(
        self,
    ) -> None:
        await self._start_turn()
        controller = self._controller()

        result = await controller.start(
            StartRunCommand(
                run_ref=self._run_ref(),
                input_text="分析上月销售趋势",
            )
        )

        self.assertEqual(result.status, HarnessStatusType.COMPLETED)
        self.assertEqual(result.phase.value, "finalization")
        self.assertEqual(result.finalization_result.final_answer, FINAL_ANSWER)
        self.assertEqual(result.finalization_result.run_ref, self._run_ref())

        # 历史落库：用户消息 + 助手消息各一条，turn 以 harness/completed 结束。
        repository = ConversationRepository(self.session_factory)
        detail = await repository.get_conversation(
            self.identity["user_id"], self.identity["conversation_id"]
        )
        self.assertEqual(len(detail["messages"]), 2)
        assistant = detail["messages"][-1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["content"], FINAL_ANSWER)
        turn = detail["turns"][0]
        self.assertEqual(turn["status"], "completed")
        self.assertEqual(turn["execution_mode"], "harness")
        self.assertIsNotNone(turn["completed_at"])

        # 终态 checkpoint 与 active_run 条件释放。
        conversation = detail["conversation"]
        self.assertIsNone(conversation["active_run_id"])
        self.assertEqual(conversation["status"], "completed")

        # 最终 checkpoint：运行现场进入 completed/finalization。
        run_state = await PostgresHarnessRunStore(self.session_factory).load_by_id(
            run_id=self.identity["run_id"], user_id=self.identity["user_id"]
        )
        harness = run_state["harness"]
        self.assertEqual(harness["status"], "completed")
        self.assertEqual(harness["phase"], "finalization")
        self.assertEqual(harness["terminal_intent"], "completed")
        self.assertEqual(harness["final_answer"], FINAL_ANSWER)

        # 账本完成：锁定内容、formation_run_id 和一次完成推进。
        ledger = PostgresFinalizationLedger(self.session_factory)
        model = await ledger.get(
            run_id=self.identity["run_id"], user_id=self.identity["user_id"]
        )
        self.assertIsNotNone(model)
        self.assertEqual(model.stage, "completed")
        self.assertEqual(model.terminal_status, "completed")
        self.assertEqual(model.final_answer, FINAL_ANSWER)
        self.assertEqual(len(model.finalization_digest), 64)
        self.assertEqual(model.attempts, 1)
        self.assertEqual(model.input_payload["user_query"], "分析上月销售趋势")
        self.assertEqual(list(model.input_payload["asset_ids"]), [])

        # 记忆形成提交：真实服务已创建形成审计，Ledger 记录了它的 ID。
        formation_runs = await self._formation_runs()
        self.assertEqual(len(formation_runs), 1)
        self.assertEqual(model.formation_run_id, formation_runs[0].formation_run_id)

        # 幂等：完成后再次对账，不新增消息或形成任务，只增加 attempts。
        finalization = PostgresFinalizationService(
            conversation_repository=repository,
            ledger=ledger,
            run_store=PostgresHarnessRunStore(self.session_factory),
            memory_formation_service=build_formation_service(self.session_factory),
        )
        replayed = await finalization.reconcile(
            run_id=self.identity["run_id"], user_id=self.identity["user_id"]
        )
        self.assertEqual(replayed.status, HarnessStatusType.COMPLETED)
        self.assertEqual(replayed.final_answer, FINAL_ANSWER)
        detail = await repository.get_conversation(
            self.identity["user_id"], self.identity["conversation_id"]
        )
        self.assertEqual(len(detail["messages"]), 2)
        self.assertEqual(len(await self._formation_runs()), 1)
        model = await ledger.get(
            run_id=self.identity["run_id"], user_id=self.identity["user_id"]
        )
        self.assertEqual(model.attempts, 2)


if __name__ == "__main__":
    unittest.main()
