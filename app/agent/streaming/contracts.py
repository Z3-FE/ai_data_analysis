"""Harness 事件流的稳定数据契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Protocol

from pydantic import Field

from app.agent.state_result_store.contracts import ContractModel, HarnessRunRef


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


__all__ = ["HarnessEvent", "HarnessEventSink"]
