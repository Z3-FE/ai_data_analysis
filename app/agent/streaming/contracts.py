"""Harness 事件流的稳定数据契约。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol

from pydantic import Field

from app.agent.state_result_store.contracts import ContractModel, HarnessRunRef


class EventType(StrEnum):
    """前后端事件协议的全部事件名；一处定义，调用点、前端与测试按值对齐。"""

    # —— run 生命周期：run.started（入口）→ 循环过程事件 → 终态四选一（controller 收口后发）
    #    → run.result（API 层把最终结果载荷发给前端）——
    RUN_STARTED = "run.started"  # start() 创建现场后发：运行开始
    RUN_RESULT = "run.result"  # API 层在 operation 返回后发：最终结果载荷（前端渲染最终答案用它）
    RUN_COMPLETED = "run.completed"  # 终态宣告：正常完成
    RUN_FAILED = "run.failed"  # 终态宣告：不可恢复失败
    RUN_TIMEOUT = "run.timeout"  # 终态宣告：超出运行级 deadline
    RUN_CANCELLED = "run.cancelled"  # 终态宣告：用户取消/断连收口
    # —— SSE 流 ——
    STREAM_FAILED = "stream.failed"
    # —— 确认 ——
    CONFIRMATION_RESOLVED = "confirmation.resolved"
    CONFIRMATION_REQUIRED = "confirmation.required"
    # —— planner ——
    PLANNER_STARTED = "planner.started"
    PLANNER_RETRYING = "planner.retrying"
    PLANNER_COMPLETED = "planner.completed"
    PLANNER_FAILED = "planner.failed"
    # —— 工具 ——
    TOOL_STARTED = "tool.started"
    TOOL_RETRYING = "tool.retrying"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_PROGRESS = "tool.progress"  # custom() 的固定归一化目标
    # —— 动作 ——
    ACTION_COMMITTED = "action.committed"
    # —— context 构建 ——
    CONTEXT_STARTED = "context.started"
    CONTEXT_FAILED = "context.failed"
    CONTEXT_MEMORY_RETRIEVED = "context.memory_retrieved"
    CONTEXT_KNOWLEDGE_RETRIEVED = "context.knowledge_retrieved"
    CONTEXT_PLAN = "context.plan"
    CONTEXT_COMPILED = "context.context_compiled"
    CONTEXT_COMPLETED = "context.completed"


class HarnessEvent(ContractModel):
    """发送给前端或其他事件消费者的受控运行事件。"""

    event_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    run_ref: HarnessRunRef
    phase: str = Field(min_length=1, max_length=64)
    iteration: int = Field(ge=0)
    action_id: str | None = Field(default=None, max_length=256)
    source: str = Field(min_length=1, max_length=128)
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class HarnessEventSink(Protocol):
    """事件发布目标；实现可以是 SSE 队列、测试收集器或空 sink。"""

    def publish(self, event: HarnessEvent) -> None: ...


__all__ = ["EventType", "HarnessEvent", "HarnessEventSink"]
