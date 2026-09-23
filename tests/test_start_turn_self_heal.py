"""start_turn 自愈前置检查：占用者已死的 active_run_id 自动释放。

背景：进程在 turn 落库与 run 创建之间（或终态收口与 active_run 释放之间）中断时，
会留下 active_run_id 永久占用、会话无法发起新运行。前置检查核实占用者已死后
就地清理遗留现场并放行；只有占用者确实在跑才拒绝。
"""

import unittest
from datetime import datetime

from sqlalchemy import select

from app.agent.state_result_store.contracts import HarnessStatusType
from app.models.agent_history import ConversationModel, ConversationTurnModel
from app.models.harness import HarnessRunModel
from app.repositories.conversation_repository import ConversationRepository
from tests.harness_postgres import (
    cleanup_user,
    create_session_factory,
    ensure_tables,
    new_run_identity,
)


def _run_row(identity: dict[str, str], status: str) -> HarnessRunModel:
    return HarnessRunModel(
        run_id=identity["run_id"],
        user_id=identity["user_id"],
        conversation_id=identity["conversation_id"],
        thread_id=identity["thread_id"],
        turn_id=identity["turn_id"],
        input_text="历史问题",
        status=status,
        phase="start_run",
    )


class StartTurnSelfHealTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine, self.session_factory = create_session_factory()
        await ensure_tables(self.engine)
        self.identity = new_run_identity("selfheal")
        self.user_id = self.identity["user_id"]

    async def asyncTearDown(self) -> None:
        await cleanup_user(self.session_factory, self.user_id)
        await self.engine.dispose()

    async def _seed_occupied(self, occupant_status: str | None) -> dict[str, str]:
        """造一个被占用的会话：active_run_id 指向 OLD 运行，OLD 的 turn 处于 running。"""
        occupant = new_run_identity("selfheal-old")
        async with self.session_factory() as session:
            session.add(
                ConversationModel(
                    conversation_id=self.identity["conversation_id"],
                    user_id=self.user_id,
                    thread_id=self.identity["thread_id"],
                    title="历史会话",
                    data_source_id="olist",
                    active_run_id=occupant["run_id"],
                    status="running",
                )
            )
            session.add(
                ConversationTurnModel(
                    turn_id=occupant["turn_id"],
                    conversation_id=self.identity["conversation_id"],
                    user_id=self.user_id,
                    thread_id=self.identity["thread_id"],
                    run_id=occupant["run_id"],
                    input_text="历史问题",
                    status="running",
                    started_at=datetime.utcnow(),
                )
            )
            if occupant_status is not None:
                session.add(_run_row(occupant, occupant_status))
            await session.commit()
        return occupant

    async def _start_new_turn(self, repository: ConversationRepository) -> None:
        await repository.start_turn(
            conversation_id=self.identity["conversation_id"],
            user_id=self.user_id,
            thread_id=self.identity["thread_id"],
            turn_id=self.identity["turn_id"],
            run_id=self.identity["run_id"],
            input_text="新问题",
        )

    async def test_hard_orphan_released_and_new_turn_registered(self) -> None:
        """硬孤儿（run 行不存在）：释放占用、遗留 turn 标失败、新 turn 正常登记。"""
        occupant = await self._seed_occupied(None)
        await self._start_new_turn(ConversationRepository(self.session_factory))
        async with self.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id
                    == self.identity["conversation_id"]
                )
            )
            self.assertEqual(conversation.active_run_id, self.identity["run_id"])
            stale_turn = await session.scalar(
                select(ConversationTurnModel).where(
                    ConversationTurnModel.run_id == occupant["run_id"]
                )
            )
            self.assertEqual(stale_turn.status, "failed")
            self.assertIsNotNone(stale_turn.completed_at)

    async def test_terminal_occupant_released(self) -> None:
        """占用者已终态但未释放：同样自愈放行，run 行保持原终态不被改写。"""
        occupant = await self._seed_occupied(HarnessStatusType.COMPLETED)
        await self._start_new_turn(ConversationRepository(self.session_factory))
        async with self.session_factory() as session:
            run = await session.scalar(
                select(HarnessRunModel).where(
                    HarnessRunModel.run_id == occupant["run_id"]
                )
            )
            self.assertEqual(run.status, HarnessStatusType.COMPLETED)
            stale_turn = await session.scalar(
                select(ConversationTurnModel).where(
                    ConversationTurnModel.run_id == occupant["run_id"]
                )
            )
            self.assertEqual(stale_turn.status, "failed")

    async def test_running_occupant_still_rejected(self) -> None:
        """占用者确实在跑：保持原语义拒绝，active_run_id 不被释放。"""
        occupant = await self._seed_occupied(HarnessStatusType.RUNNING)
        repository = ConversationRepository(self.session_factory)
        with self.assertRaises(ValueError):
            await self._start_new_turn(repository)
        async with self.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id
                    == self.identity["conversation_id"]
                )
            )
            self.assertEqual(conversation.active_run_id, occupant["run_id"])


if __name__ == "__main__":
    unittest.main()
