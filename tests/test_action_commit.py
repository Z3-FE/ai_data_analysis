"""切片 B 动作提交的最小顺序测试。"""

import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.agent.loop_controller.action_commit import ActionCommitRequest
from app.agent.state_result_store.contracts import (
    ActionType,
    HarnessRunRef,
    NextAction,
    RunExecutionFence,
)
from tests.fakes.slice_b.fake_action_committer import FakeActionCommitter


class ActionCommitTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.run_ref = HarnessRunRef(
            user_id="user-1",
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            run_id="run-1",
        )

    def request(self, action_seq: int = 1, expected_action_seq: int | None = None) -> ActionCommitRequest:
        return ActionCommitRequest(
            run_ref=self.run_ref,
            action=NextAction(
                action_seq=action_seq,
                action_type=ActionType.FINAL_ANSWER,
                final_answer="完成",
            ),
            expected_action_seq=action_seq if expected_action_seq is None else expected_action_seq,
            expected_state_version=2,
            expected_checkpoint_revision=0,
            execution_fence=RunExecutionFence(
                owner_id="test-worker",
                fencing_token=1,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
        )

    async def test_events_are_prepared_checkpoint_committed(self) -> None:
        committer = FakeActionCommitter()
        result = await committer.commit(self.request())
        self.assertEqual(result.status, "committed")
        self.assertEqual(
            committer.events,
            [("prepared", 1), ("checkpoint", 1), ("committed", 1)],
        )

    def test_expected_sequence_must_match_action(self) -> None:
        with self.assertRaises((ValidationError, ValueError)):
            self.request(action_seq=2, expected_action_seq=1)


if __name__ == "__main__":
    unittest.main()
