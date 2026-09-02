"""PostgreSQL 记忆事实存储。

这里是记忆层的第一版真实基础设施实现：PostgreSQL 保存完整记忆事实，
后续 Qdrant 只作为可重建的语义检索索引，不改变本模块的读写契约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    Index,
    String,
    Text,
    desc,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

from .interfaces import MemoryProvider
from .models import (
    MemoryItem,
    MemoryMatch,
    MemoryReadRequest,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryType,
    utc_now,
)


def _utc_now() -> datetime:
    """返回带时区的 UTC 时间，供 ORM 默认值和更新操作复用。"""
    return utc_now()


class AgentMemoryModel(Base):
    """四类记忆共用的 PostgreSQL 事实表。"""

    __tablename__ = "agent_memories"
    __table_args__ = (
        # 约束记忆类型，防止非法字符串进入事实表。
        CheckConstraint(
            "memory_type IN ('working', 'episodic', 'semantic', 'perceptual')",
            name="ck_agent_memories_type",
        ),
        # 约束记忆生命周期状态，统一 Provider 的读取语义。
        CheckConstraint(
            "status IN ('active', 'archived', 'superseded', 'deleted', 'conflict')",
            name="ck_agent_memories_status",
        ),
        # 重要性和可信度都必须是 0 到 1 的评分。
        CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_agent_memories_importance",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_agent_memories_confidence",
        ),
        # 失效时间不能早于生效时间，避免形成无效的时间区间。
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_agent_memories_valid_range",
        ),
        Index(
            "idx_agent_memories_scope",
            "user_id",
            "tenant_id",
            "agent_id",
            "project_id",
            "conversation_id",
        ),
        Index("idx_agent_memories_type_status", "memory_type", "status"),
        {"comment": "Data Agent 四类记忆事实"},
    )

    # 记忆事实的唯一 ID，也是 Qdrant 索引回查 PostgreSQL 的主键。
    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 记忆类型：working、episodic、semantic 或 perceptual。
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 提供给检索和模型上下文的自然语言内容。
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # 所属用户 ID，所有记忆的最低隔离边界。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # 所属租户 ID，多租户场景使用；单租户阶段允许为空。
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 所属 Agent ID，用于区分 Data Agent 和其他 Agent 的记忆空间。
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 所属项目 ID，用于隔离同一用户的不同项目记忆。
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 所属会话 ID；为空表示可跨会话复用的用户、项目或 Agent 记忆。
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 来源类别，例如 conversation_message、analysis_output 或 asset。
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # 来源对象 ID，例如 message_id、turn_id 或 asset_id。
    source_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 产生记忆的会话轮次 ID。
    source_turn_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 产生记忆的原始消息 ID。
    source_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 来源附加数据，例如提取器名称、版本和引用位置。
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # 按记忆类型扩展的结构化数据，例如 asset_id、task_id 或 preference_key。
    structured_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    # 存储、提取、索引和整合过程的元数据，不作为主要记忆内容。
    memory_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    # 记忆生命周期状态，默认只检索 active。
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MemoryStatus.ACTIVE.value,
        server_default=MemoryStatus.ACTIVE.value,
        index=True,
    )
    # 业务重要性评分，范围为 0 到 1。
    importance: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5"
    )
    # 记忆可信度评分，范围为 0 到 1。
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5"
    )

    # 记忆首次写入时间，用于排序和生命周期管理。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    # 记忆最后一次更新或整合时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utc_now,
        server_default=func.now(),
    )
    # 记忆开始生效的时间，可用于事实版本管理。
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 记忆失效时间；为空表示没有设置结束时间。
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PostgreSQLMemoryProvider(MemoryProvider):
    """基于 SQLAlchemy 异步 Session 的记忆事实 Provider。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 复用 agent_app 已有的 PostgreSQL Session 工厂。
        self.session_factory = session_factory

    async def add(self, item: MemoryItem) -> MemoryItem:
        """新增记忆事实，并返回数据库写入后的领域对象。"""
        model = _model_from_item(item)
        async with self.session_factory() as session:
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return _item_from_model(model)

    async def search(self, request: MemoryReadRequest) -> list[MemoryMatch]:
        """按作用域、类型、资源和关键词读取 active 记忆。"""
        statement = select(AgentMemoryModel).where(
            *_scope_filters(
                request.scope,
                exact_conversation=request.exact_conversation,
            ),
            AgentMemoryModel.memory_type.in_(
                memory_type.value for memory_type in request.memory_types
            ),
        )
        if request.include_archived:
            statement = statement.where(
                AgentMemoryModel.status.in_(
                    [MemoryStatus.ACTIVE.value, MemoryStatus.ARCHIVED.value]
                )
            )
        else:
            statement = statement.where(
                AgentMemoryModel.status == MemoryStatus.ACTIVE.value
            )

        if request.query:
            # 第一版使用 PostgreSQL ILIKE，后续可替换为 Qdrant，不改变 Provider 接口。
            statement = statement.where(
                AgentMemoryModel.content.ilike(_like_pattern(request.query), escape="\\")
            )

        if request.asset_ids:
            statement = statement.where(
                or_(
                    *(
                        AgentMemoryModel.structured_data["asset_id"].as_string()
                        == asset_id
                        for asset_id in request.asset_ids
                    )
                )
            )

        statement = statement.order_by(
            desc(AgentMemoryModel.updated_at),
            desc(AgentMemoryModel.created_at),
        ).limit(request.limit)
        async with self.session_factory() as session:
            models = list((await session.scalars(statement)).all())

        retrieval_mode = "keyword" if request.query else "exact"
        return [
            MemoryMatch(
                item=_item_from_model(model),
                retrieval_mode=retrieval_mode,
            )
            for model in models
        ]

    async def update(self, item: MemoryItem) -> MemoryItem:
        """更新作用域内已有记忆，找不到或越权时明确报错。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(AgentMemoryModel).where(
                    AgentMemoryModel.memory_id == item.memory_id
                )
            )
            if model is None:
                raise LookupError(f"记忆不存在：{item.memory_id}")
            if not _model_scope_matches(model, item.scope):
                raise PermissionError("不能更新其他作用域的记忆")
            _copy_item_to_model(item, model)
            await session.commit()
            await session.refresh(model)
            return _item_from_model(model)

    async def archive(
        self,
        memory_id: str,
        *,
        scope: MemoryScope,
        reason: str | None = None,
    ) -> MemoryItem | None:
        """把作用域内记忆标记为 archived，不删除原始事实。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(AgentMemoryModel).where(
                    AgentMemoryModel.memory_id == memory_id,
                    *_scope_filters(scope, require_exact=True),
                )
            )
            if model is None:
                return None
            model.status = MemoryStatus.ARCHIVED.value
            model.memory_metadata = {
                **(model.memory_metadata or {}),
                "archive_reason": reason or "unspecified",
            }
            model.updated_at = _utc_now()
            await session.commit()
            await session.refresh(model)
            return _item_from_model(model)


def _model_from_item(item: MemoryItem) -> AgentMemoryModel:
    """把领域模型转换成 ORM 模型。"""
    model = AgentMemoryModel(
        memory_id=item.memory_id,
        memory_type=item.memory_type.value,
        content=item.content,
        user_id=item.scope.user_id,
        tenant_id=item.scope.tenant_id,
        agent_id=item.scope.agent_id,
        project_id=item.scope.project_id,
        conversation_id=item.scope.conversation_id,
        source_type=item.source.source_type,
        source_id=item.source.source_id,
        source_turn_id=item.source.turn_id,
        source_message_id=item.source.message_id,
        source_metadata=dict(item.source.metadata),
        structured_data=dict(item.structured_data),
        memory_metadata=dict(item.metadata),
        status=item.status.value,
        importance=item.importance,
        confidence=item.confidence,
        created_at=item.created_at,
        updated_at=item.updated_at,
        valid_from=item.valid_from,
        valid_to=item.valid_to,
    )
    return model


def _copy_item_to_model(item: MemoryItem, model: AgentMemoryModel) -> None:
    """把领域模型的全部可更新字段同步到 ORM 实例。"""
    model.memory_type = item.memory_type.value
    model.content = item.content
    model.tenant_id = item.scope.tenant_id
    model.agent_id = item.scope.agent_id
    model.project_id = item.scope.project_id
    model.conversation_id = item.scope.conversation_id
    model.source_type = item.source.source_type
    model.source_id = item.source.source_id
    model.source_turn_id = item.source.turn_id
    model.source_message_id = item.source.message_id
    model.source_metadata = dict(item.source.metadata)
    model.structured_data = dict(item.structured_data)
    model.memory_metadata = dict(item.metadata)
    model.status = item.status.value
    model.importance = item.importance
    model.confidence = item.confidence
    model.updated_at = _utc_now()
    model.valid_from = item.valid_from
    model.valid_to = item.valid_to


def _item_from_model(model: AgentMemoryModel) -> MemoryItem:
    """把 ORM 模型转换成记忆层领域对象。"""
    return MemoryItem(
        memory_id=model.memory_id,
        memory_type=MemoryType(model.memory_type),
        content=model.content,
        scope=MemoryScope(
            user_id=model.user_id,
            tenant_id=model.tenant_id,
            agent_id=model.agent_id,
            project_id=model.project_id,
            conversation_id=model.conversation_id,
        ),
        source=MemorySource(
            source_type=model.source_type,
            source_id=model.source_id,
            turn_id=model.source_turn_id,
            message_id=model.source_message_id,
            metadata=model.source_metadata or {},
        ),
        structured_data=model.structured_data or {},
        metadata=model.memory_metadata or {},
        status=MemoryStatus(model.status),
        importance=model.importance,
        confidence=model.confidence,
        created_at=model.created_at,
        updated_at=model.updated_at,
        valid_from=model.valid_from,
        valid_to=model.valid_to,
    )


def _scope_filters(
    scope: MemoryScope,
    *,
    require_exact: bool = False,
    exact_conversation: bool = False,
) -> list[Any]:
    """生成作用域过滤条件；Working 可单独要求会话 ID 精确匹配。"""
    filters: list[Any] = [AgentMemoryModel.user_id == scope.user_id]
    for column_name in ("tenant_id", "agent_id", "project_id", "conversation_id"):
        column = getattr(AgentMemoryModel, column_name)
        value = getattr(scope, column_name)
        if column_name == "conversation_id" and exact_conversation:
            filters.append(column == value)
            continue
        if require_exact or value is None:
            filters.append(column.is_(None) if value is None else column == value)
        else:
            filters.append(or_(column.is_(None), column == value))
    return filters


def _model_scope_matches(model: AgentMemoryModel, scope: MemoryScope) -> bool:
    """更新前做 Python 层严格作用域校验，避免修改其他范围的记录。"""
    return all(
        getattr(model, column_name) == getattr(scope, column_name)
        for column_name in (
            "user_id",
            "tenant_id",
            "agent_id",
            "project_id",
            "conversation_id",
        )
    )


def _like_pattern(query: str) -> str:
    """转义 ILIKE 通配符，避免用户输入改变关键词查询语义。"""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


__all__ = ["AgentMemoryModel", "PostgreSQLMemoryProvider"]
