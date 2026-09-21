"""Harness 事件写出器与 SSE 队列 —— 运行事件通道的生产侧。

一次 Harness 运行的所有进度/结果事件都经 ``HarnessEventWriter`` 统一写出：
盖章（run_ref/event_id/时间戳/上下文）→ 清洗（``_sanitize``）→ 非阻塞入队
（``QueueEventSink``）。API 层（harness.py 的 ``_sse_response``）是消费侧：
从队列取事件、经 ``format_sse`` 变成 SSE 帧推给前端。

组件一览：
- ``_DROP_KEYS`` / ``_sanitize``：payload 清洗，只留进度展示需要的有限 JSON
- ``QueueEventSink``：入队适配器，兼留存全量事件供执行轨迹落库
- ``HarnessEventWriter``：控制器与工具使用的统一事件出口（含 bind 上下文）
- ``NullHarnessEventWriter``：非流式接口的空写出器
- ``stream_done_marker`` / ``format_sse``：SSE 消费侧的结束标记与帧格式化
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Iterator
from uuid import uuid4

from app.agent.state_result_store.contracts import HarnessRunRef, LoopPhaseStatusType

from .contracts import EventType, HarnessEvent, HarnessEventSink

logger = logging.getLogger(__name__)

# 这些 payload 键携带原始行数据/SQL/提示词等大体量或敏感内容，对前端进度展示
# 无用，清洗时直接剔除（防止数据泄漏与事件膨胀）。
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
# SSE 流结束哨兵：运行收尾（含失败）后由 API 层放入队列，消费侧见到即收流；绝不对外序列化。
_STREAM_DONE = object()


def _sanitize(value: Any, *, depth: int = 0) -> Any:
    """清洗 payload：只保留适合前端进度展示的有限 JSON 数据。

    规则：嵌套最多 3 层；字符串截断到 2000 字符；映射最多 64 键并剔除
    ``_DROP_KEYS`` 中的大字段；列表最多 32 项；其余类型一律 ``str()[:500]``。
    """
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
    """事件入队适配器 —— SSE 通道生产侧的最后一跳。

    双职责：
    1. ``put_nowait`` 非阻塞入队（SSE 队列与 Harness worker 位于同一事件循环，
       入队不能阻塞工具执行）；
    2. ``events`` 留存全量事件，运行结束后由 API 层整体落库为执行轨迹
       （save_execution_trace）。
    """

    def __init__(self, queue: asyncio.Queue[Any]) -> None:
        self.queue = queue
        self.events: list[HarnessEvent] = []

    def publish(self, event: HarnessEvent) -> None:
        # SSE 队列与 Harness worker 位于同一事件循环；入队不能阻塞工具执行。
        self.events.append(event)
        self.queue.put_nowait(event)


class HarnessEventWriter:
    """Harness 内部统一事件入口 —— 控制器与工具唯一的事件写出通道。

    每次发布补全 run_ref/event_id/时间戳，从 bind() 上下文填充 source/phase/
    iteration/action_id，清洗 payload 后交给 sink 非阻塞发布。
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
        # 唯一必填参数（位置传参）：事件名必须取自 EventType 枚举——协议一处定义，拼错直接报错
        event_type: EventType,
        *,
        source: str | None = None,  # 来源标识；不传走 bind() 上下文，再回退 "harness"
        phase: LoopPhaseStatusType | None = None,  # 状态机阶段；不传走 bind() 上下文，再回退 START_RUN
        iteration: int | None = None,  # 循环轮数（前端"第 N 轮"）；不传走 bind() 上下文，再回退 0
        action_id: str | None = None,  # 关联的动作（Planner 的 action_seq 体系）；不传走 bind() 上下文，再回退 None
        payload: Mapping[str, Any] | None = None,  # 业务数据 dict；发送前经 _sanitize 清洗（截断、剔 rows/sql/code 等大字段）
    ) -> HarnessEvent | None:  # 成功返回已发布事件；失败吞异常返回 None，调用方一般不用接
        """发布一个受控 Harness 事件；失败只记日志并返回 None，绝不反噬调用方。

        显式参数优先，未传时回退 bind() 上下文；构造与入队失败统一在此吞掉。
        """
        try:
            context = self._context.get()
            effective_source = str(source or context.get("source") or "harness")
            effective_phase = str(phase or context.get("phase") or LoopPhaseStatusType.START_RUN)
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
        except Exception:
            logger.exception(
                "Harness event emission failed: run_id=%s event_type=%s",
                self.run_ref.run_id,
                event_type,
            )
            return None

    def custom(self, payload: Mapping[str, Any]) -> HarnessEvent | None:
        """把 LangGraph custom 事件归一化为前端稳定的 tool.progress。

        payload 里的 ``type`` 提升为 ``custom_type``（前端按它分类图标/文案），
        ``node`` 并入 source（形如 ``tool:{node}``），其余字段透传（经清洗）。
        """
        value = dict(payload)
        custom_type = str(value.pop("type", "progress"))
        node = str(value.get("node", "tool"))
        context = self._context.get()
        source = f"{context.get('source', 'tool')}:{node}"
        return self.emit(
            EventType.TOOL_PROGRESS,
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
        phase: LoopPhaseStatusType | None = None,
        iteration: int | None = None,
        action_id: str | None = None,
    ) -> Iterator["HarnessEventWriter"]:
        """为工具内部事件绑定当前动作上下文。

        上下文存在本 writer 独有的 ContextVar 里（名字带 run_id 便于排查）：
        with 块内的 emit() 不必逐次传 source/phase/iteration/action_id，
        退出时自动恢复原值。
        """
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
    """非流式接口使用的空事件写出器。

    事件照常构造（emit 返回 HarnessEvent | None），但 _NullEventSink 直接丢弃，
    不进队列也不留存轨迹。
    """

    def __init__(self, *, run_ref: HarnessRunRef) -> None:
        super().__init__(run_ref=run_ref, sink=_NullEventSink())


class _NullEventSink:
    def publish(self, event: HarnessEvent) -> None:
        return None


def stream_done_marker() -> object:
    """返回 SSE 内部结束标记；不对外序列化。

    API 层在运行任务收尾（成功/失败都算）时把它放进队列，
    消费侧收到即停止读流。
    """
    return _STREAM_DONE


def format_sse(event: HarnessEvent) -> str:
    """把受控事件格式化为标准 SSE 帧。

    三行结构 ``id:``/``event:``/``data:``（JSON 序列化、保中文），帧尾空行分隔。
    """
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


__all__ = [
    "HarnessEventWriter",
    "NullHarnessEventWriter",
    "QueueEventSink",
    "format_sse",
    "stream_done_marker",
]
