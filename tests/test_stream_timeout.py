"""验证流式响应的空闲超时，而不是整个流的累计时长。"""

import asyncio
import unittest

from app.agent.utils.stream_timeout import astream_with_idle_timeout


class StreamIdleTimeoutTest(unittest.IsolatedAsyncioTestCase):
    """流式事件持续到达时允许累计耗时超过空闲阈值。"""

    async def test_each_event_resets_idle_timeout(self) -> None:
        async def stream():
            yield "first"
            await asyncio.sleep(0.02)
            yield "second"
            await asyncio.sleep(0.02)
            yield "third"

        events = [
            item
            async for item in astream_with_idle_timeout(stream(), timeout_seconds=0.03)
        ]

        self.assertEqual(events, ["first", "second", "third"])

    async def test_no_event_for_timeout_is_failed(self) -> None:
        async def stream():
            yield "first"
            await asyncio.sleep(0.05)
            yield "second"

        with self.assertRaisesRegex(TimeoutError, "连续 0.03 秒没有返回"):
            _ = [
                item
                async for item in astream_with_idle_timeout(
                    stream(), timeout_seconds=0.03
                )
            ]


if __name__ == "__main__":
    unittest.main()
