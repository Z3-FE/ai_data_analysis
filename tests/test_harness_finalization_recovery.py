"""Finalization Ledger 的崩溃对账矩阵。

每个用例用真实 PostgreSQL 复现一次"操作与账本推进之间崩溃"的窗口，
再通过 reconcile() 从账本当前阶段恢复，验证不重复写入、不伪造终态、
且恢复过程不回到 Planner、Tool Runtime 或 ContextEngine。
"""

import unittest

from sqlalchemy import select

from app.agent.finalization.errors import FinalizationFailure
from app.agent.finalization.service import PostgresFinalizationService
from app.agent.loop_controller.contracts import FinalizationInput, StartRunCommand
from app.agent.loop_controller.controller import LoopController
from app.agent.state_result_store.contracts import (
    HarnessRunRef,
    HarnessStatus,
)
from app.models.agent_history import ConversationMessageModel
from app.models.memory import MemoryFormationRunModel
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.finalization_repository import (
    FinalizationDigestConflict,
    FinalizationLedgerError,
    PostgresFinalizationLedger,
)
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

FINAL_ANSWER = "最终分析结论已经生成。"
_TERMINAL_STATUSES = {
    HarnessStatus.COMPLETED,
    HarnessStatus.FAILED,
    HarnessStatus.CANCELLED,
    HarnessStatus.TIMEOUT,
}


class _FinishTurnFailingRepository:
    """模拟历史写入提交前的进程崩溃。"""

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def finish_turn(self, **kwargs) -> bool:
        raise RuntimeError("模拟历史写入崩溃")

    def __getattr__(self, name):
        return getattr(self._repository, name)


class _ReleaseFailingRepository:
    """模拟 active_run 释放前的进程崩溃。"""

    def __init__(self, repository: ConversationRepository) -> None:
        self._repository = repository

    async def release_active_run(self, **kwargs) -> bool:
        raise RuntimeError("模拟释放崩溃")

    def __getattr__(self, name):
        return getattr(self._repository, name)


class _TerminalSaveFailingRunStore:
    """模拟终态 checkpoint 写入前的进程崩溃；普通阶段保存不受影响。"""

    def __init__(self, run_store: PostgresHarnessRunStore) -> None:
        self._run_store = run_store

    async def save(self, run_ref: HarnessRunRef, state) -> None:
        if HarnessStatus(state["harness"]["status"]) in _TERMINAL_STATUSES:
            raise RuntimeError("模拟终态 checkpoint 崩溃")
        await self._run_store.save(run_ref, state)

    def __getattr__(self, name):
        return getattr(self._run_store, name)


class _CompletedAdvanceBlockedLedger:
    """模拟账本最后一步推进前的进程崩溃。"""

    def __init__(self, ledger: PostgresFinalizationLedger) -> None:
        self._ledger = ledger

    async def advance(self, *, run_id: str, user_id: str, stage: str, **kwargs):
        if stage == "completed":
            raise RuntimeError("模拟账本完成步崩溃")
        return await self._ledger.advance(
            run_id=run_id, user_id=user_id, stage=stage, **kwargs
        )

    def __getattr__(self, name):
        return getattr(self._ledger, name)


class _SubmitFailingFormationService:
    """模拟记忆形成提交前的进程崩溃。"""

    async def submit(self, turn):
        raise RuntimeError("模拟记忆形成崩溃")


class FinalizationRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine, self.session_factory = create_session_factory()
        await ensure_tables(self.engine)
        self.identity = new_run_identity("fin")
        self.ledger = PostgresFinalizationLedger(self.session_factory)
        self.run_store = PostgresHarnessRunStore(self.session_factory)
        self.repository = ConversationRepository(self.session_factory)
        await self.repository.ensure_conversation(
            self.identity["conversation_id"], self.identity["user_id"]
        )
        await self.repository.start_turn(
            conversation_id=self.identity["conversation_id"],
            user_id=self.identity["user_id"],
            thread_id=self.identity["thread_id"],
            turn_id=self.identity["turn_id"],
            run_id=self.identity["run_id"],
            input_text="分析上月销售趋势",
        )

    async def asyncTearDown(self) -> None:
        await cleanup_user(self.session_factory, self.identity["user_id"])
        await self.engine.dispose()

    def _run_ref(self) -> HarnessRunRef:
        return HarnessRunRef(**self.identity)

    def _service(
        self,
        *,
        conversation_repository=None,
        run_store=None,
        ledger=None,
        formation="real",
    ) -> PostgresFinalizationService:
        formation_service = (
            build_formation_service(self.session_factory)
            if formation == "real"
            else formation
        )
        return PostgresFinalizationService(
            conversation_repository=conversation_repository or self.repository,
            ledger=ledger or self.ledger,
            run_store=run_store or self.run_store,
            memory_formation_service=formation_service,
        )

    def _controller(self, service: PostgresFinalizationService) -> LoopController:
        context_engine, _, _ = _engine(FakeMemoryReader())
        return LoopController(
            context_builder=context_engine,
            planning_agent=FakePlanningAgent(final_answer=FINAL_ANSWER),
            finalization_service=service,
            run_store=self.run_store,
        )

    async def _start_run(self, service: PostgresFinalizationService) -> None:
        await self._controller(service).start(
            StartRunCommand(
                run_ref=self._run_ref(), input_text="分析上月销售趋势"
            )
        )

    async def _reconcile(self) -> None:
        return await self._service().reconcile(
            run_id=self.identity["run_id"], user_id=self.identity["user_id"]
        )

    async def _ledger_row(self):
        return await self.ledger.get(
            run_id=self.identity["run_id"], user_id=self.identity["user_id"]
        )

    async def _harness_status(self) -> str:
        state = await self.run_store.load_by_id(
            run_id=self.identity["run_id"], user_id=self.identity["user_id"]
        )
        return state["harness"]["status"]

    async def _assistant_messages(self) -> list:
        async with self.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(ConversationMessageModel).where(
                            ConversationMessageModel.turn_id == self.identity["turn_id"],
                            ConversationMessageModel.role == "assistant",
                        )
                    )
                ).all()
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

    async def _active_run_id(self):
        detail = await self.repository.get_conversation(
            self.identity["user_id"], self.identity["conversation_id"]
        )
        return detail["conversation"]["active_run_id"]

    def test_http_error_maps_finalization_failure_to_503(self) -> None:
        from app.api.routers import harness as harness_router

        failure = FinalizationFailure(self._run_ref(), "released")
        self.assertEqual(harness_router._http_error(failure).status_code, 503)
        conflict = FinalizationLedgerError("收口内容冲突")
        self.assertEqual(harness_router._http_error(conflict).status_code, 409)

    async def test_reconcile_requires_existing_ledger(self) -> None:
        with self.assertRaises(FinalizationLedgerError):
            await self._reconcile()

    async def test_crash_before_history_write_keeps_running_and_recovers(self) -> None:
        service = self._service(
            conversation_repository=_FinishTurnFailingRepository(self.repository)
        )

        with self.assertRaises(FinalizationFailure) as ctx:
            await self._start_run(service)

        self.assertEqual(ctx.exception.stage, "prepared")
        self.assertEqual((await self._ledger_row()).stage, "prepared")
        self.assertEqual(await self._harness_status(), "running")
        self.assertEqual(await self._assistant_messages(), [])
        self.assertIsNotNone(await self._active_run_id())

        result = await self._reconcile()

        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        self.assertEqual(result.final_answer, FINAL_ANSWER)
        self.assertEqual(len(await self._assistant_messages()), 1)
        self.assertIsNone(await self._active_run_id())
        row = await self._ledger_row()
        self.assertEqual(row.stage, "completed")
        self.assertEqual(row.attempts, 2)
        self.assertEqual(len(await self._formation_runs()), 1)

    async def test_crash_before_terminal_checkpoint_keeps_running_and_recovers(
        self,
    ) -> None:
        service = self._service(run_store=_TerminalSaveFailingRunStore(self.run_store))

        with self.assertRaises(FinalizationFailure) as ctx:
            await self._start_run(service)

        self.assertEqual(ctx.exception.stage, "history_saved")
        self.assertEqual((await self._ledger_row()).stage, "history_saved")
        self.assertEqual(await self._harness_status(), "running")
        # 红线：终态 checkpoint 未完成，active_run 不允许释放。
        self.assertIsNotNone(await self._active_run_id())

        result = await self._reconcile()

        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        self.assertEqual(await self._harness_status(), "completed")
        self.assertEqual(len(await self._assistant_messages()), 1)
        self.assertIsNone(await self._active_run_id())
        self.assertEqual((await self._ledger_row()).stage, "completed")

    async def test_crash_before_release_keeps_checkpoint_and_recovers(self) -> None:
        service = self._service(
            conversation_repository=_ReleaseFailingRepository(self.repository)
        )

        with self.assertRaises(FinalizationFailure) as ctx:
            await self._start_run(service)

        self.assertEqual(ctx.exception.stage, "checkpoint_saved")
        self.assertEqual((await self._ledger_row()).stage, "checkpoint_saved")
        # 终态 checkpoint 已经落库，但会话占用尚未释放。
        self.assertEqual(await self._harness_status(), "completed")
        self.assertIsNotNone(await self._active_run_id())

        result = await self._reconcile()

        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        self.assertIsNone(await self._active_run_id())
        self.assertEqual(len(await self._assistant_messages()), 1)
        self.assertEqual((await self._ledger_row()).stage, "completed")

    async def test_crash_before_formation_submit_recovers_with_real_submit(
        self,
    ) -> None:
        service = self._service(formation=_SubmitFailingFormationService())

        with self.assertRaises(FinalizationFailure) as ctx:
            await self._start_run(service)

        self.assertEqual(ctx.exception.stage, "released")
        self.assertEqual((await self._ledger_row()).stage, "released")
        self.assertEqual(await self._formation_runs(), [])

        result = await self._reconcile()

        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        formation_runs = await self._formation_runs()
        self.assertEqual(len(formation_runs), 1)
        row = await self._ledger_row()
        self.assertEqual(row.stage, "completed")
        self.assertEqual(row.formation_run_id, formation_runs[0].formation_run_id)

    async def test_crash_before_ledger_completion_recovers_without_new_formation(
        self,
    ) -> None:
        service = self._service(ledger=_CompletedAdvanceBlockedLedger(self.ledger))

        with self.assertRaises(FinalizationFailure) as ctx:
            await self._start_run(service)

        self.assertEqual(ctx.exception.stage, "formation_submitted")
        self.assertEqual((await self._ledger_row()).stage, "formation_submitted")
        self.assertEqual(len(await self._formation_runs()), 1)

        result = await self._reconcile()

        self.assertEqual(result.status, HarnessStatus.COMPLETED)
        # 形成步骤已经完成，对账不允许再次提交形成任务。
        self.assertEqual(len(await self._formation_runs()), 1)
        self.assertEqual((await self._ledger_row()).stage, "completed")

    async def test_different_finalize_content_is_rejected_by_digest(self) -> None:
        service = self._service(
            conversation_repository=_FinishTurnFailingRepository(self.repository)
        )
        with self.assertRaises(FinalizationFailure):
            await self._start_run(service)

        conflicting = FinalizationInput(
            run_ref=self._run_ref(),
            user_query="分析上月销售趋势",
            final_answer="另一个版本的结果",
            terminal_status=HarnessStatus.COMPLETED,
        )
        with self.assertRaises(FinalizationDigestConflict):
            await service.finalize(conflicting)

    async def test_repeated_reconcile_after_completion_is_idempotent(self) -> None:
        await self._start_run(self._service())
        first = await self._reconcile()
        second = await self._reconcile()

        self.assertEqual(first.status, HarnessStatus.COMPLETED)
        self.assertEqual(second.status, HarnessStatus.COMPLETED)
        self.assertEqual(second.final_answer, FINAL_ANSWER)
        self.assertEqual(len(await self._assistant_messages()), 1)
        self.assertEqual(len(await self._formation_runs()), 1)
        row = await self._ledger_row()
        self.assertEqual(row.stage, "completed")
        # 两次对账各记一次 attempts；首次 finalize 完成步再记一次。
        self.assertEqual(row.attempts, 3)


if __name__ == "__main__":
    unittest.main()
