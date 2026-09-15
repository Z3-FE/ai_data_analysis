"""Harness SSE 空闲心跳边界测试。"""

from __future__ import annotations

import asyncio
import unittest

from app.agent.loop_controller.contracts import FinalizationResult, LoopRunResult
from app.agent.state_result_store.contracts import (
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
)
from app.agent.streaming.writer import HarnessEventWriter, QueueEventSink
from app.api.routers.harness import _sse_response


class HarnessStreamingTest(unittest.IsolatedAsyncioTestCase):
    """确认 Harness 业务事件空闲时仍会发送 SSE 注释心跳。"""

    async def test_sse_emits_comment_heartbeat_without_business_event(self) -> None:
        run_ref = HarnessRunRef(
            user_id="user-1",
            conversation_id="conversation-1",
            thread_id="thread-1",
            turn_id="turn-1",
            run_id="run-1",
        )
        queue: asyncio.Queue[object] = asyncio.Queue()
        writer = HarnessEventWriter(run_ref=run_ref, sink=QueueEventSink(queue))

        async def operation() -> LoopRunResult:
            await asyncio.sleep(0.03)
            return LoopRunResult(
                run_ref=run_ref,
                status=HarnessStatus.COMPLETED,
                phase=LoopPhase.FINALIZATION,
                iteration=0,
                finalization_result=FinalizationResult(
                    run_ref=run_ref,
                    status=HarnessStatus.COMPLETED,
                    final_answer="完成",
                ),
            )

        response = _sse_response(
            operation,
            run_ref=run_ref,
            queue=queue,
            writer=writer,
            heartbeat_seconds=0.01,
        )
        chunks = [chunk async for chunk in response.body_iterator]

        self.assertIn(": heartbeat\n\n", chunks)
        self.assertTrue(any("event: run.result" in chunk for chunk in chunks))
        self.assertFalse(any(chunk.startswith("data: : heartbeat") for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
