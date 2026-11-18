"""进程中断后遗留的过期运行：新请求前按 timeout 结束并释放会话。"""

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agent.state_result_store.contracts import (
    HarnessRunRef,
    HarnessStatusType,
    LoopPhaseStatusType,
)
from app.agent.state_result_store.state import (
    new_harness_control_state,
    transition_harness_state,
)
from app.api.routers.harness import _finish_expired_active_run
from app.models.agent_history import ConversationModel, ConversationTurnModel
from app.models.harness import HarnessFinalizationModel
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.harness_run_repository import PostgresHarnessRunStore
from tests.harness_postgres import (
    cleanup_user,
    create_session_factory,
    ensure_tables,
    new_run_identity,
)


class ExpiredRunCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine, self.session_factory = create_session_factory()
        await ensure_tables(self.engine)
        self.identity = new_run_identity("expired")
        self.repository = ConversationRepository(self.session_factory)
        self.run_store = PostgresHarnessRunStore(self.session_factory)

    async def asyncTearDown(self) -> None:
        await cleanup_user(self.session_factory, self.identity["user_id"])
        await self.engine.dispose()

    async def _seed_stuck_run(self, *, deadline_offset: timedelta) -> HarnessRunRef:
        """模拟进程在 build_context 被杀：turn、active_run、running 运行都留在库里。"""
        await self.repository.start_turn(
            conversation_id=self.identity["conversation_id"],
            user_id=self.identity["user_id"],
            thread_id=self.identity["thread_id"],
            turn_id=self.identity["turn_id"],
            run_id=self.identity["run_id"],
            input_text="你好",
        )
        now = datetime.now(UTC)
        started_at = now - timedelta(hours=1)
        harness = new_harness_control_state(
            original_goal="你好",
            started_at=started_at.isoformat(),
            deadline_at=(now + deadline_offset).isoformat(),
        )
        harness = transition_harness_state(
            harness,
            status=HarnessStatusType.RUNNING,
            phase=LoopPhaseStatusType.BUILD_CONTEXT,
        )
        run_ref = HarnessRunRef(**self.identity)
        await self.run_store.create(
            run_ref,
            {
                "input_text": "你好",
                "original_question": "你好",
                **self.identity,
                "project_id": None,
                "asset_ids": [],
                "harness": harness,
            },
        )
        return run_ref

    async def _cleanup(self) -> None:
        await _finish_expired_active_run(
            session_factory=self.session_factory,
            conversation_id=self.identity["conversation_id"],
            user_id=self.identity["user_id"],
        )

    async def _start_next_turn(self) -> None:
        next_identity = new_run_identity("expired-next")
        await self.repository.start_turn(
            conversation_id=self.identity["conversation_id"],
            user_id=self.identity["user_id"],
            thread_id=self.identity["thread_id"],
            turn_id=next_identity["turn_id"],
            run_id=next_identity["run_id"],
            input_text="再问一次",
        )

    async def test_expired_running_run_is_finalized_and_conversation_released(self) -> None:
        run_ref = await self._seed_stuck_run(deadline_offset=-timedelta(minutes=5))

        await self._cleanup()

        state = await self.run_store.load(run_ref)
        self.assertEqual(state["harness"]["status"], HarnessStatusType.TIMEOUT.value)
        self.assertEqual(state["harness"]["last_error"]["code"], "run_deadline_exceeded")
        async with self.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id == run_ref.conversation_id
                )
            )
            turn = await session.scalar(
                select(ConversationTurnModel).where(
                    ConversationTurnModel.turn_id == run_ref.turn_id
                )
            )
            ledger = await session.scalar(
                select(HarnessFinalizationModel).where(
                    HarnessFinalizationModel.run_id == run_ref.run_id
                )
            )
        self.assertIsNone(conversation.active_run_id)
        self.assertEqual(turn.status, HarnessStatusType.TIMEOUT.value)
        self.assertEqual(ledger.stage, "completed")
        await self._start_next_turn()

    async def test_run_inside_grace_period_still_blocks_new_turn(self) -> None:
        run_ref = await self._seed_stuck_run(deadline_offset=-timedelta(seconds=10))

        await self._cleanup()

        state = await self.run_store.load(run_ref)
        self.assertEqual(state["harness"]["status"], HarnessStatusType.RUNNING.value)
        with self.assertRaises(ValueError):
            await self._start_next_turn()

    async def test_unexpired_run_still_blocks_new_turn(self) -> None:
        run_ref = await self._seed_stuck_run(deadline_offset=timedelta(minutes=10))

        await self._cleanup()

        state = await self.run_store.load(run_ref)
        self.assertEqual(state["harness"]["status"], HarnessStatusType.RUNNING.value)
        with self.assertRaises(ValueError):
            await self._start_next_turn()


if __name__ == "__main__":
    unittest.main()
