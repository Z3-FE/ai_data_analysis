"""为异步流提供按事件间隔计算的空闲超时。"""

import asyncio
from collections.abc import AsyncIterator
from typing import TypeVar

T = TypeVar("T")


async def astream_with_idle_timeout(
    stream: AsyncIterator[T],
    timeout_seconds: float,
) -> AsyncIterator[T]:
    """消费异步流，并限制等待下一条事件的最长时间。

    计时只覆盖当前 __anext__ 的等待过程。每收到一条事件，计时就会重新开始，
    因此 timeout_seconds 表示连续无返回的最长时间，而不是整个流的累计时长。
    """
    iterator = stream.__aiter__()
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=timeout_seconds,
                )
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"流式响应空闲超时（连续 {timeout_seconds:g} 秒没有返回）。"
                ) from exc
            yield item
    finally:
        # 超时或调用方提前停止时，主动关闭底层流，释放 HTTP 连接。
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()
