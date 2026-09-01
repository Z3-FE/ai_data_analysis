"""Data Agent 记忆层的通用数据契约。

本模块只描述记忆，不负责数据库读写、向量检索或模型调用。具体存储实现通过
``MemoryProvider`` 接口接入，避免业务节点依赖 PostgreSQL、Qdrant 等基础设施。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    """返回带时区的 UTC 时间。"""
    return datetime.now(timezone.utc)


class MemoryType(StrEnum):
    """Hello-Agents 第 8 章采用的四类记忆。"""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PERCEPTUAL = "perceptual"


class MemoryStatus(StrEnum):
    """记忆的生命周期状态。"""

    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    CONFLICT = "conflict"


@dataclass(slots=True, frozen=True)
class MemoryScope:
    """记忆的访问边界，用于阻止用户、会话和 Agent 之间串用。"""

    user_id: str
    tenant_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    conversation_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "user_id",
            "tenant_id",
            "agent_id",
            "project_id",
            "conversation_id",
        ):
            value = getattr(self, name)
            normalized = _normalize_optional_text(value)
            if name == "user_id" and normalized is None:
                raise ValueError("MemoryScope.user_id 不能为空")
            object.__setattr__(self, name, normalized)

    def to_dict(self) -> dict[str, str]:
        """返回只包含已设置字段的作用域字典。"""
        return {
            name: value
            for name in (
                "user_id",
                "tenant_id",
                "agent_id",
                "project_id",
                "conversation_id",
            )
            if (value := getattr(self, name)) is not None
        }


@dataclass(slots=True, frozen=True)
class MemorySource:
    """记忆来源，支持从记忆追溯到原始消息、轮次或资源。"""

    source_type: str
    source_id: str | None = None
    turn_id: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_type = _normalize_optional_text(self.source_type)
        if source_type is None:
            raise ValueError("MemorySource.source_type 不能为空")
        object.__setattr__(self, "source_type", source_type)
        for name in ("source_id", "turn_id", "message_id"):
            object.__setattr__(self, name, _normalize_optional_text(getattr(self, name)))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "turn_id": self.turn_id,
            "message_id": self.message_id,
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class MemoryItem:
    """一条可持久化、检索和整合的记忆事实。"""

    memory_type: MemoryType
    content: str
    scope: MemoryScope
    source: MemorySource
    memory_id: str = field(default_factory=lambda: str(uuid4()))
    structured_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    status: MemoryStatus = MemoryStatus.ACTIVE
    importance: float = 0.5
    confidence: float = 0.5
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        self.memory_id = str(self.memory_id).strip()
        self.content = str(self.content).strip()
        if not self.memory_id:
            raise ValueError("MemoryItem.memory_id 不能为空")
        if not self.content:
            raise ValueError("MemoryItem.content 不能为空")
        self.memory_type = MemoryType(self.memory_type)
        self.status = MemoryStatus(self.status)
        self.importance = _normalize_score(self.importance, "importance")
        self.confidence = _normalize_score(self.confidence, "confidence")
        self.structured_data = dict(self.structured_data)
        self.metadata = dict(self.metadata)
        for name in ("created_at", "updated_at", "valid_from", "valid_to"):
            value = getattr(self, name)
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            if value is not None and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            setattr(self, name, value)
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("MemoryItem.valid_to 不能早于 valid_from")

    def to_dict(self) -> dict[str, Any]:
        """转换为存储层、日志和调试接口可使用的字典。"""
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type.value,
            "content": self.content,
            "scope": self.scope.to_dict(),
            "source": self.source.to_dict(),
            "structured_data": dict(self.structured_data),
            "metadata": dict(self.metadata),
            "status": self.status.value,
            "importance": self.importance,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


@dataclass(slots=True, frozen=True)
class MemoryReadRequest:
    """一次记忆读取请求。空 query 表示按作用域精确读取而非语义搜索。"""

    scope: MemoryScope
    query: str = ""
    memory_types: frozenset[MemoryType] = field(
        default_factory=lambda: frozenset(MemoryType)
    )
    asset_ids: tuple[str, ...] = ()
    limit: int = 20
    include_archived: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", str(self.query).strip())
        object.__setattr__(
            self,
            "memory_types",
            frozenset(MemoryType(value) for value in self.memory_types),
        )
        object.__setattr__(
            self,
            "asset_ids",
            tuple(
                normalized
                for value in self.asset_ids
                if (normalized := _normalize_optional_text(value)) is not None
            ),
        )
        if not self.memory_types:
            raise ValueError("MemoryReadRequest.memory_types 不能为空")
        if self.limit <= 0:
            raise ValueError("MemoryReadRequest.limit 必须大于 0")


@dataclass(slots=True)
class MemoryMatch:
    """记忆检索结果；score 由具体检索实现提供。"""

    item: MemoryItem
    score: float | None = None
    retrieval_mode: str = "exact"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.score is not None:
            self.score = _normalize_score(self.score, "score")
        self.retrieval_mode = str(self.retrieval_mode).strip() or "exact"
        self.metadata = dict(self.metadata)


def _normalize_optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_score(value: float, field_name: str) -> float:
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} 必须在 0 到 1 之间")
    return normalized


__all__ = [
    "MemoryItem",
    "MemoryMatch",
    "MemoryReadRequest",
    "MemoryScope",
    "MemorySource",
    "MemoryStatus",
    "MemoryType",
]
