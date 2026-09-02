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

    # 当前会话中仍然有效的活动状态、约束和资源引用。
    WORKING = "working"
    # 已经完成的任务经历、处理结果和可复用经验。
    EPISODIC = "episodic"
    # 相对稳定的事实、业务约定、指标定义和用户偏好。
    SEMANTIC = "semantic"
    # 图片、音频和文件的资源身份及其派生观察。
    PERCEPTUAL = "perceptual"


class MemoryStatus(StrEnum):
    """记忆的生命周期状态。"""

    # 当前可被正常读取和检索的记忆。
    ACTIVE = "active"
    # 暂时不参与默认检索，但保留记录以便审计或恢复。
    ARCHIVED = "archived"
    # 已被更新版本替代的旧记忆。
    SUPERSEDED = "superseded"
    # 逻辑删除的记忆，默认不可读取。
    DELETED = "deleted"
    # 与已有记忆存在冲突，等待确认或人工处理。
    CONFLICT = "conflict"


@dataclass(slots=True, frozen=True)
class MemoryScope:
    """记忆的访问边界，用于阻止用户、会话和 Agent 之间串用。"""

    # 必填的用户身份；所有记忆至少归属于一个用户。
    user_id: str
    # 可选的租户身份，用于多租户数据隔离。
    tenant_id: str | None = None
    # 可选的 Agent 身份，用于区分不同 Agent 的记忆空间。
    agent_id: str | None = None
    # 可选的项目身份，用于区分同一用户的不同项目上下文。
    project_id: str | None = None
    # 可选的会话身份；设置后表示记忆只属于当前 conversation。
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

    # 来源类别，例如 conversation_message、analysis_output 或 asset。
    source_type: str
    # 来源对象的业务 ID，例如 message_id、turn_id 或 asset_id。
    source_id: str | None = None
    # 产生这条记忆的会话轮次 ID。
    turn_id: str | None = None
    # 产生这条记忆的原始消息 ID。
    message_id: str | None = None
    # 来源的附加信息，例如提取器名称和版本。
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

    # 记忆所属类型，决定默认读取策略和生命周期。
    memory_type: MemoryType
    # 给模型和检索器使用的自然语言记忆内容。
    content: str
    # 访问这条记忆所需的用户、Agent、项目和会话作用域。
    scope: MemoryScope
    # 记忆的可追溯来源，不允许只保存没有依据的事实。
    source: MemorySource
    # 记忆的唯一 ID，也是向量索引和外部引用使用的主键。
    memory_id: str = field(default_factory=lambda: str(uuid4()))
    # 记忆类型对应的结构化字段，例如分析任务 ID、偏好键或 asset_id。
    structured_data: dict[str, Any] = field(default_factory=dict)
    # 存储、提取器、索引等实现层元数据，不作为主要记忆内容。
    metadata: dict[str, Any] = field(default_factory=dict)
    # 记忆当前的生命周期状态。
    status: MemoryStatus = MemoryStatus.ACTIVE
    # 业务重要性评分，默认 0.5 表示尚未人为提高或降低优先级。
    importance: float = 0.5
    # 记忆内容的可信度评分，默认 0.5 表示中性可信度。
    confidence: float = 0.5
    # 记忆首次创建时间，用于排序和生命周期管理。
    created_at: datetime = field(default_factory=utc_now)
    # 记忆最近一次更新或整合时间。
    updated_at: datetime = field(default_factory=utc_now)
    # 记忆开始生效的时间，可用于事实版本管理。
    valid_from: datetime | None = None
    # 记忆失效的时间；为空表示尚未设置结束时间。
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

    # 本次读取必须使用的作用域，Provider 不能跨作用域返回记忆。
    scope: MemoryScope
    # 语义检索文本；为空时只做类型、资源和作用域过滤。
    query: str = ""
    # 允许本次读取的记忆类型；默认允许四类记忆。
    memory_types: frozenset[MemoryType] = field(
        default_factory=lambda: frozenset(MemoryType)
    )
    # 需要精确读取的资源 ID，例如用户追问“刚才那张图”。
    asset_ids: tuple[str, ...] = ()
    # Provider 最多返回的记忆条数。
    limit: int = 20
    # 是否把已归档记忆纳入本次读取；默认只读活动记忆。
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

    # 命中的记忆事实。
    item: MemoryItem
    # 检索相关性分数；精确读取可以不提供分数。
    score: float | None = None
    # 命中方式，例如 exact、keyword 或 vector。
    retrieval_mode: str = "exact"
    # 检索器返回的附加信息，例如距离、索引版本或过滤条件。
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
