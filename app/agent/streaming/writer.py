"""Harness 事件写出器和 SSE 队列。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Iterator
from uuid import uuid4

from app.agent.state_result_store.contracts import HarnessRunRef

from .contracts import HarnessEvent, HarnessEventSink

_DROP_KEYS = {
    "rows",
    "sql_result",
    "display_rows",
    "display_sql_result",
    "sql",
    "query",
    "python_code",
    "code",
    "analysis_plan",
    "analysis_task_results",
    "analysis_evidence",
    "calculation_result",
    "result",
    "output",
    "messages",
    "prompt",
    "reasoning",
    "content",
}
_STREAM_DONE = object()


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    """只保留适合前端进度展示的有限 JSON 数据。"""
    if depth > 3:
        return str(value)[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:2_000]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            key_text = str(key)
            if key_text in _DROP_KEYS:
                continue
            result[key_text] = _sanitize(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, depth=depth + 1) for item in list(value)[:32]]
    return str(value)[:500]


class QueueEventSink:
    """把事件以非阻塞方式放入 SSE 队列。"""

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self.queue = queue

    def publish(self, event: HarnessEvent) -> None:
        # SSE 队列与 Harness worker 位于同一事件循环；入队不能阻塞工具执行。
        self.queue.put_nowait(event)


class HarnessEventWriter:
    """Harness 内部统一事件入口。

    LangGraph 的 ``stream_writer`` 是同步回调，所以该类只做非阻塞入队；
    SSE 序列化和网络写出留给 API 层。
    """

    def __init__(self, *, run_ref: HarnessRunRef, sink: HarnessEventSink) -> None:
        self.run_ref = run_ref
        self.sink = sink
        self._context: ContextVar[dict[str, Any]] = ContextVar(
            f"harness_event_context_{run_ref.run_id}", default={}
        )

    def emit(
        self,
        event_type: str,
        *,
        source: str | None = None,
        phase: str | None = None,
        iteration: int | None = None,
        action_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> HarnessEvent:
        """发布一个受控 Harness 事件。"""
        context = self._context.get()
        effective_source = str(source or context.get("source") or "harness")
        effective_phase = str(phase or context.get("phase") or "start_run")
        effective_iteration = int(
            iteration if iteration is not None else context.get("iteration", 0)
        )
        effective_action_id = (
            action_id if action_id is not None else context.get("action_id")
        )
        event = HarnessEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            run_ref=self.run_ref,
            phase=effective_phase,
            iteration=effective_iteration,
            action_id=effective_action_id,
            source=effective_source,
            timestamp=datetime.now(UTC),
            payload=_sanitize(dict(payload or {})),
        )
        self.sink.publish(event)
        return event

    def custom(self, payload: Mapping[str, Any]) -> HarnessEvent:
        """把 LangGraph custom 事件归一化为前端稳定的 tool.progress。"""
        value = dict(payload)
        custom_type = str(value.pop("type", "progress"))
        node = str(value.get("node", "tool"))
        context = self._context.get()
        source = f"{context.get('source', 'tool')}:{node}"
        return self.emit(
            "tool.progress",
            source=source,
            payload={
                "custom_type": custom_type,
                **value,
            },
        )

    @contextmanager
    def bind(
        self,
        *,
        source: str | None = None,
        phase: str | None = None,
        iteration: int | None = None,
        action_id: str | None = None,
    ) -> Iterator["HarnessEventWriter"]:
        """为工具内部事件绑定当前动作上下文。"""
        current = dict(self._context.get())
        if source is not None:
            current["source"] = source
        if phase is not None:
            current["phase"] = phase
        if iteration is not None:
            current["iteration"] = iteration
        if action_id is not None:
            current["action_id"] = action_id
        token = self._context.set(current)
        try:
            yield self
        finally:
            self._context.reset(token)


class NullHarnessEventWriter(HarnessEventWriter):
    """非流式接口使用的空事件写出器。"""

    def __init__(self, *, run_ref: HarnessRunRef) -> None:
        super().__init__(run_ref=run_ref, sink=_NullEventSink())


class _NullEventSink:
    def publish(self, event: HarnessEvent) -> None:
        return None


def stream_done_marker() -> object:
    """返回 SSE 内部结束标记；不对外序列化。"""
    return _STREAM_DONE


def format_sse(event: HarnessEvent) -> str:
    """把受控事件格式化为标准 SSE 帧。"""
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


__all__ = [
    "HarnessEventWriter",
    "NullHarnessEventWriter",
    "QueueEventSink",
    "format_sse",
    "stream_done_marker",
]
