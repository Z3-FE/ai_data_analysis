"""ContextEngine 的通用数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    """返回带时区的 UTC 时间，避免不同 Agent 的时间字段无法比较。"""
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ContextItem:
    """一条可参与上下文编译的候选信息。"""

    item_id: str
    content: str
    source_type: str
    source_ref: str | None = None
    scope: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    importance: float = 0.5
    token_count: int = 0
    relevance_score: float | None = None
    section: str = "context"
    always_include: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.item_id = str(self.item_id).strip()
        self.content = str(self.content).strip()
        self.source_type = str(self.source_type).strip() or "unknown"
        self.section = str(self.section).strip() or "context"
        if not self.item_id:
            raise ValueError("ContextItem.item_id 不能为空")
        if not self.content:
            raise ValueError("ContextItem.content 不能为空")
        if self.source_ref is not None:
            self.source_ref = str(self.source_ref).strip() or None
        self.importance = min(1.0, max(0.0, float(self.importance)))
        if self.relevance_score is not None:
            self.relevance_score = min(1.0, max(0.0, float(self.relevance_score)))
        self.token_count = max(0, int(self.token_count))
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        self.scope = {str(key): str(value) for key, value in self.scope.items()}
        self.metadata = dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        """转换成适合调试、接口返回和日志记录的字典。"""
        return {
            "item_id": self.item_id,
            "content": self.content,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "scope": dict(self.scope),
            "created_at": self.created_at.isoformat(),
            "importance": self.importance,
            "token_count": self.token_count,
            "relevance_score": self.relevance_score,
            "section": self.section,
            "always_include": self.always_include,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ContextRequest:
    """一次上下文编译请求及其隔离范围。"""

    current_input: str
    token_budget: int = 8000
    user_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None
    node_id: str | None = None
    task_id: str | None = None
    scope: dict[str, str] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        self.current_input = str(self.current_input).strip()
        self.token_budget = int(self.token_budget)
        if self.token_budget <= 0:
            raise ValueError("ContextRequest.token_budget 必须大于 0")
        self.scope = {str(key): str(value) for key, value in self.scope.items()}
        for key in (
            "user_id",
            "conversation_id",
            "agent_id",
            "node_id",
            "task_id",
        ):
            value = getattr(self, key)
            if value is None or key not in self.scope:
                continue
            if self.scope[key] != str(value):
                raise ValueError(f"ContextRequest.{key} 与 scope 中的值冲突")

    def effective_scope(self) -> dict[str, str]:
        """合并常用身份字段和自定义作用域。"""
        scope = dict(self.scope)
        for key in (
            "user_id",
            "conversation_id",
            "agent_id",
            "node_id",
            "task_id",
        ):
            value = getattr(self, key)
            if value is not None:
                scope.setdefault(key, str(value))
        return scope


@dataclass(slots=True)
class ContextPolicy:
    """一个 Agent 或节点的上下文选择策略。"""

    allowed_source_types: frozenset[str] | None = None
    protected_source_types: frozenset[str] = frozenset()
    protected_item_ids: frozenset[str] = frozenset()
    section_order: tuple[str, ...] = (
        "task",
        "state",
        "evidence",
        "history",
        "context",
    )
    section_titles: dict[str, str] = field(default_factory=dict)
    min_relevance: float = 0.0
    relevance_weight: float = 0.6
    recency_weight: float = 0.2
    importance_weight: float = 0.2
    recency_half_life_seconds: float = 86_400.0
    max_items: int | None = None
    compression_marker: str = "[内容已压缩]"

    def __post_init__(self) -> None:
        if self.allowed_source_types is not None:
            self.allowed_source_types = frozenset(self.allowed_source_types)
        self.protected_source_types = frozenset(self.protected_source_types)
        self.protected_item_ids = frozenset(self.protected_item_ids)
        self.min_relevance = min(1.0, max(0.0, float(self.min_relevance)))
        weights = (
            self.relevance_weight,
            self.recency_weight,
            self.importance_weight,
        )
        if any(weight < 0 for weight in weights) or sum(weights) <= 0:
            raise ValueError("ContextPolicy 的评分权重必须为非负且总和大于 0")
        if self.recency_half_life_seconds <= 0:
            raise ValueError("recency_half_life_seconds 必须大于 0")
        if self.max_items is not None and self.max_items <= 0:
            raise ValueError("max_items 必须大于 0")
        self.section_titles = dict(self.section_titles)


@dataclass(slots=True)
class ContextSection:
    """结构化后的上下文分区。"""

    name: str
    title: str
    items: list[ContextItem]
    content: str
    token_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "items": [item.to_dict() for item in self.items],
            "content": self.content,
            "token_count": self.token_count,
        }


@dataclass(slots=True)
class ContextTrace:
    """一次上下文编译的可解释追踪信息。"""

    request_id: str
    candidate_count: int
    isolated_count: int
    selected_count: int
    token_budget: int
    token_used: int
    compression_applied: bool
    selected_item_ids: list[str] = field(default_factory=list)
    dropped_items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "candidate_count": self.candidate_count,
            "isolated_count": self.isolated_count,
            "selected_count": self.selected_count,
            "token_budget": self.token_budget,
            "token_used": self.token_used,
            "compression_applied": self.compression_applied,
            "selected_item_ids": list(self.selected_item_ids),
            "dropped_items": list(self.dropped_items),
        }


@dataclass(slots=True)
class CompiledContext:
    """ContextEngine 编译后的结构化上下文和 Agent 输入消息。"""

    request: ContextRequest
    messages: list[dict[str, str]]
    sections: list[ContextSection]
    selected_items: list[ContextItem]
    source_refs: list[str]
    token_usage: dict[str, int]
    dropped_items: list[dict[str, Any]]
    compression_applied: bool
    trace: ContextTrace

    def to_messages(self) -> list[dict[str, str]]:
        """返回可传给 LangChain/OpenAI 适配器的消息副本。"""
        return [dict(message) for message in self.messages]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request.request_id,
            "messages": self.to_messages(),
            "sections": [section.to_dict() for section in self.sections],
            "selected_items": [item.to_dict() for item in self.selected_items],
            "source_refs": list(self.source_refs),
            "token_usage": dict(self.token_usage),
            "dropped_items": list(self.dropped_items),
            "compression_applied": self.compression_applied,
            "trace": self.trace.to_dict(),
        }
