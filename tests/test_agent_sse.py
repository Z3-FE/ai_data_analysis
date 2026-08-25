"""Agent SSE 心跳与业务事件转发测试。"""

import asyncio
import json
import unittest

from app.services.agent_service import _stream_sse_with_heartbeat


class AgentSseHeartbeatTest(unittest.IsolatedAsyncioTestCase):
    """验证长时间无业务事件时连接仍保持活动。"""

    async def test_emits_heartbeat_without_creating_business_event(self) -> None:
        async def events():
            await asyncio.sleep(0.03)
            yield {
                "type": "progress",
                "step": "测试步骤",
                "node": "test_node",
                "status": "success",
            }

        chunks = [
            chunk
            async for chunk in _stream_sse_with_heartbeat(
                events(), heartbeat_seconds=0.01
            )
        ]

        self.assertTrue(any(chunk == ": heartbeat\n\n" for chunk in chunks))
        data_chunks = [chunk for chunk in chunks if chunk.startswith("data: ")]
        self.assertEqual(len(data_chunks), 1)
        self.assertEqual(
            json.loads(data_chunks[0].removeprefix("data: "))["type"],
            "progress",
        )

    async def test_stream_error_is_returned_as_sse_error_event(self) -> None:
        async def events():
            yield {
                "type": "progress",
                "step": "测试步骤",
                "node": "test_node",
                "status": "running",
            }
            raise RuntimeError("测试错误")

        chunks = [
            chunk
            async for chunk in _stream_sse_with_heartbeat(
                events(), heartbeat_seconds=0.01
            )
        ]

        self.assertEqual(len(chunks), 2)
        error = json.loads(chunks[-1].removeprefix("data: "))
        self.assertEqual(
            error,
            {
                "type": "error",
                "step": "Agent 服务错误",
                "node": "agent_service",
                "message": "测试错误",
            },
        )


if __name__ == "__main__":
    unittest.main()
