"""PostgreSQL 记忆事实仓储。"""

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import MemoryAsset, MemoryCreate, MemoryRecord
from app.models.memory import (
    AgentMemoryModel,
    MemoryAssetModel,
    MemoryGraphProjectionModel,
    MemoryIndexJobModel,
    MemorySourceModel,
)


def _utcnow() -> datetime:
    """返回无时区 UTC，匹配当前数据库表的 TIMESTAMP 类型。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _terms(text: str) -> set[str]:
    """按连续中英文数字片段和单个中文字符生成轻量检索词。"""
    tokens = set(re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", text.lower()))
    return tokens or {text.lower()} if text else set()


def _record(model: AgentMemoryModel) -> MemoryRecord:
    """把 ORM 记录转换为不暴露 SQLAlchemy 的领域对象。"""
    return MemoryRecord(
        memory_id=model.memory_id,
        user_id=model.user_id,
        memory_type=MemoryType(model.memory_type),
        scope=MemoryScope(model.scope),
        conversation_id=model.conversation_id,
        project_id=model.project_id,
        content=model.content,
        structured_data=dict(model.structured_data or {}),
        status=MemoryStatus(model.status),
        version=model.version,
        importance=float(model.importance),
        confidence=float(model.confidence),
        supersedes_memory_id=model.supersedes_memory_id,
        expires_at=model.expires_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        access_count=model.access_count,
        last_accessed_at=model.last_accessed_at,
    )


def _asset(model: MemoryAssetModel) -> MemoryAsset:
    """把附件 ORM 记录转换为领域对象。"""
    return MemoryAsset(
        asset_id=model.asset_id,
        user_id=model.user_id,
        conversation_id=model.conversation_id,
        project_id=model.project_id,
        modality=model.modality,
        file_name=model.file_name,
        mime_type=model.mime_type,
        storage_uri=model.storage_uri,
        extracted_text=model.extracted_text,
        extraction_status=model.extraction_status,
        index_status=model.index_status,
        metadata=dict(model.asset_metadata or {}),
        encoder_name=model.encoder_name,
        embedding_dimension=model.embedding_dimension,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _validate_create(request: MemoryCreate) -> None:
    """在进入事务前校验长期记忆创建约束。"""
    if not request.user_id.strip():
        raise ValueError("user_id 不能为空")
    if request.memory_type is MemoryType.WORKING:
        raise ValueError("Working Memory 不能写入 agent_memories")
    if not request.content.strip():
        raise ValueError("记忆内容不能为空")
    if not 0 <= request.importance <= 1 or not 0 <= request.confidence <= 1:
        raise ValueError("importance/confidence 必须在 0 到 1 之间")
    if request.version < 1:
        raise ValueError("version 必须大于等于 1")
    if request.scope is MemoryScope.CONVERSATION and not request.conversation_id:
        raise ValueError("conversation 作用域的记忆必须提供 conversation_id")
    if request.scope is MemoryScope.PROJECT and not request.project_id:
        raise ValueError("project 作用域的记忆必须提供 project_id")
    if request.scope is not MemoryScope.PROJECT and request.project_id:
        raise ValueError("只有 project 作用域的记忆可以绑定 project_id")


def _new_memory_model(request: MemoryCreate) -> AgentMemoryModel:
    """把已经校验的领域请求转换为 ORM 对象。"""
    now = _utcnow()
    return AgentMemoryModel(
        memory_id=request.memory_id or str(uuid4()),
        user_id=request.user_id.strip(),
        memory_type=request.memory_type.value,
        scope=request.scope.value,
        conversation_id=request.conversation_id,
        project_id=request.project_id,
        content=request.content.strip(),
        structured_data=request.structured_data,
        status=MemoryStatus.ACTIVE.value,
        importance=request.importance,
        confidence=request.confidence,
        version=request.version,
        supersedes_memory_id=request.supersedes_memory_id,
        expires_at=request.expires_at,
        created_at=now,
        updated_at=now,
    )


def _add_sources(session: AsyncSession, memory_id: str, request: MemoryCreate) -> None:
    """把领域来源列表加入当前事务。"""
    for source in request.sources:
        session.add(
            MemorySourceModel(
                source_link_id=str(uuid4()),
                memory_id=memory_id,
                source_type=source.source_type,
                source_id=source.source_id,
                source_path=source.source_path,
            )
        )


class PostgresMemoryRepository:
    """负责记忆事实、来源、附件和同步任务的 PostgreSQL 读写。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 由应用级 PostgreSQL 客户端注入，和会话历史共用连接池但不共用业务表。
        self.session_factory = session_factory

    async def create(self, request: MemoryCreate) -> MemoryRecord:
        """创建事实记录并保存来源关联。"""
        _validate_create(request)
        memory = _new_memory_model(request)
        async with self.session_factory() as session:
            session.add(memory)
            await session.flush()
            _add_sources(session, memory.memory_id, request)
            await session.commit()
            await session.refresh(memory)
        return _record(memory)

    async def replace(
        self, memory_id: str, user_id: str, request: MemoryCreate
    ) -> tuple[MemoryRecord, MemoryRecord]:
        """在一个事务中创建新版本，并把旧版本标为 superseded。"""
        _validate_create(request)
        if request.user_id.strip() != user_id:
            raise ValueError("替换请求的 user_id 与目标用户不一致")
        async with self.session_factory() as session:
            old_model = await session.scalar(
                select(AgentMemoryModel)
                .where(
                    AgentMemoryModel.memory_id == memory_id,
                    AgentMemoryModel.user_id == user_id,
                )
                .with_for_update()
            )
            if old_model is None:
                raise LookupError("要替换的记忆不存在")
            if old_model.status != MemoryStatus.ACTIVE.value:
                raise ValueError("只有 active 记忆可以创建替代版本")
            if old_model.memory_type != request.memory_type.value:
                raise ValueError("不能使用不同的 memory_type 替换记忆")
            if (
                old_model.scope != request.scope.value
                or old_model.conversation_id != request.conversation_id
                or old_model.project_id != request.project_id
            ):
                raise ValueError("替代版本必须保持原记忆的作用域和归属")

            old_record = _record(old_model)
            replacement = MemoryCreate(
                user_id=request.user_id,
                memory_type=request.memory_type,
                content=request.content,
                scope=request.scope,
                conversation_id=request.conversation_id,
                project_id=request.project_id,
                structured_data=request.structured_data,
                importance=request.importance,
                confidence=request.confidence,
                expires_at=request.expires_at,
                sources=request.sources,
                memory_id=request.memory_id,
                version=old_model.version + 1,
                supersedes_memory_id=old_model.memory_id,
            )
            new_model = _new_memory_model(replacement)
            if new_model.memory_id == old_model.memory_id:
                raise ValueError("替代版本必须使用新的 memory_id")
            old_model.status = MemoryStatus.SUPERSEDED.value
            old_model.updated_at = _utcnow()
            session.add(new_model)
            await session.flush()
            _add_sources(session, new_model.memory_id, replacement)
            await session.commit()
            await session.refresh(new_model)
            return old_record, _record(new_model)

    async def get(self, memory_id: str, user_id: str) -> MemoryRecord | None:
        """按 ID 和用户读取 active/superseded 等任意生命周期记录。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(AgentMemoryModel).where(
                    AgentMemoryModel.memory_id == memory_id,
                    AgentMemoryModel.user_id == user_id,
                )
            )
            return _record(model) if model else None

    async def get_by_id(self, memory_id: str) -> MemoryRecord | None:
        """按 ID 读取记录，仅供索引同步等内部任务使用。"""
        async with self.session_factory() as session:
            model = await session.get(AgentMemoryModel, memory_id)
            return _record(model) if model else None

    async def touch_access(self, memory_ids: list[str], user_id: str) -> None:
        """更新检索命中统计，不改变记忆正文和版本。"""
        if not memory_ids:
            return
        async with self.session_factory() as session:
            await session.execute(
                update(AgentMemoryModel)
                .where(
                    AgentMemoryModel.memory_id.in_(memory_ids),
                    AgentMemoryModel.user_id == user_id,
                )
                .values(
                    access_count=AgentMemoryModel.access_count + 1,
                    last_accessed_at=_utcnow(),
                )
            )
            await session.commit()

    async def search(
        self,
        *,
        user_id: str,
        memory_type: MemoryType,
        query: str,
        limit: int,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
    ) -> list[tuple[MemoryRecord, float]]:
        """在数据库内先按权限和状态筛选，再进行可解释词项匹配。"""
        now = _utcnow()
        async with self.session_factory() as session:
            conditions = [
                AgentMemoryModel.user_id == user_id,
                AgentMemoryModel.memory_type == memory_type.value,
                AgentMemoryModel.status == MemoryStatus.ACTIVE.value,
                or_(
                    AgentMemoryModel.expires_at.is_(None),
                    AgentMemoryModel.expires_at > now,
                ),
            ]
            if conversation_id:
                visible_scopes = [AgentMemoryModel.scope == MemoryScope.USER.value]
                visible_scopes.append(
                    and_(
                        AgentMemoryModel.scope == MemoryScope.CONVERSATION.value,
                        AgentMemoryModel.conversation_id == conversation_id,
                    )
                )
                if project_id:
                    visible_scopes.append(
                        and_(
                            AgentMemoryModel.scope == MemoryScope.PROJECT.value,
                            AgentMemoryModel.project_id == project_id,
                        )
                    )
                conditions.append(or_(*visible_scopes))
            elif project_id:
                conditions.append(
                    or_(
                        AgentMemoryModel.scope == MemoryScope.USER.value,
                        and_(
                            AgentMemoryModel.scope == MemoryScope.PROJECT.value,
                            AgentMemoryModel.project_id == project_id,
                        ),
                    )
                )
            else:
                conditions.append(AgentMemoryModel.scope == MemoryScope.USER.value)
            if modality is not None:
                conditions.append(
                    AgentMemoryModel.structured_data["modality"].as_string() == modality
                )
            models = (
                await session.scalars(
                    select(AgentMemoryModel)
                    .where(and_(*conditions))
                    .order_by(desc(AgentMemoryModel.updated_at))
                    .limit(max(100, limit * 20))
                )
            ).all()
        query_terms = _terms(query)
        scored: list[tuple[MemoryRecord, float]] = []
        for model in models:
            content_terms = _terms(model.content)
            overlap = len(query_terms & content_terms) / max(1, len(query_terms))
            substring = 1.0 if query.strip().lower() in model.content.lower() else 0.0
            score = max(overlap, substring)
            if score > 0 or not query.strip():
                scored.append((_record(model), score))
        scored.sort(
            key=lambda item: (item[1], item[0].importance, item[0].updated_at),
            reverse=True,
        )
        return scored[: max(0, limit)]

    async def update(
        self, memory_id: str, user_id: str, **changes: Any
    ) -> MemoryRecord | None:
        """原地更新可变字段；需要替换语义时可通过新 ID + supersedes 建版本链。"""
        allowed = {
            "importance",
            "confidence",
            "expires_at",
        }
        values = {
            key: value
            for key, value in changes.items()
            if key in allowed and value is not None
        }
        if "importance" in values and not 0 <= values["importance"] <= 1:
            raise ValueError("importance 必须在 0 到 1 之间")
        if "confidence" in values and not 0 <= values["confidence"] <= 1:
            raise ValueError("confidence 必须在 0 到 1 之间")
        values["updated_at"] = _utcnow()
        async with self.session_factory() as session:
            model = await session.scalar(
                select(AgentMemoryModel).where(
                    AgentMemoryModel.memory_id == memory_id,
                    AgentMemoryModel.user_id == user_id,
                )
            )
            if model is None:
                return None
            for key, value in values.items():
                setattr(model, key, value)
            await session.commit()
            await session.refresh(model)
            return _record(model)

    async def mark_status(
        self, memory_id: str, user_id: str, status: MemoryStatus
    ) -> bool:
        """更新生命周期状态，不物理删除事实。"""
        async with self.session_factory() as session:
            result = await session.execute(
                update(AgentMemoryModel)
                .where(
                    AgentMemoryModel.memory_id == memory_id,
                    AgentMemoryModel.user_id == user_id,
                )
                .values(status=status.value, updated_at=_utcnow())
            )
            await session.commit()
            return bool(result.rowcount)

    async def list_expired(self, limit: int = 100) -> list[MemoryRecord]:
        """列出已经过期但仍为 active 的记录。"""
        async with self.session_factory() as session:
            models = (
                await session.scalars(
                    select(AgentMemoryModel)
                    .where(
                        AgentMemoryModel.status == MemoryStatus.ACTIVE.value,
                        AgentMemoryModel.expires_at.is_not(None),
                        AgentMemoryModel.expires_at <= _utcnow(),
                    )
                    .limit(max(0, limit))
                )
            ).all()
        return [_record(model) for model in models]

    async def save_asset(self, asset: MemoryAsset) -> MemoryAsset:
        """创建或更新附件元数据。"""
        async with self.session_factory() as session:
            model = await session.get(MemoryAssetModel, asset.asset_id)
            if model is None:
                model = MemoryAssetModel(
                    asset_id=asset.asset_id,
                    user_id=asset.user_id,
                    conversation_id=asset.conversation_id,
                    project_id=asset.project_id,
                    modality=asset.modality,
                    file_name=asset.file_name,
                    mime_type=asset.mime_type,
                    storage_uri=asset.storage_uri,
                    extracted_text=asset.extracted_text,
                    extraction_status=asset.extraction_status,
                    encoder_name=asset.encoder_name,
                    embedding_dimension=asset.embedding_dimension,
                    index_status=asset.index_status,
                    asset_metadata=asset.metadata,
                )
                session.add(model)
            elif model.user_id != asset.user_id:
                raise PermissionError("不能修改其他用户的附件")
            else:
                for field in (
                    "conversation_id",
                    "project_id",
                    "modality",
                    "file_name",
                    "mime_type",
                    "storage_uri",
                    "extracted_text",
                    "extraction_status",
                    "encoder_name",
                    "embedding_dimension",
                    "index_status",
                ):
                    setattr(model, field, getattr(asset, field))
                model.asset_metadata = asset.metadata
                model.updated_at = _utcnow()
            await session.commit()
            await session.refresh(model)
            return _asset(model)

    async def get_asset(self, asset_id: str, user_id: str) -> MemoryAsset | None:
        """按附件 ID 和用户读取元数据，供历史附件引用解析使用。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(MemoryAssetModel).where(
                    MemoryAssetModel.asset_id == asset_id,
                    MemoryAssetModel.user_id == user_id,
                )
            )
            return _asset(model) if model else None

    async def update_asset(
        self, asset_id: str, user_id: str, **changes: Any
    ) -> MemoryAsset | None:
        """按用户更新附件处理和索引状态。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(MemoryAssetModel).where(
                    MemoryAssetModel.asset_id == asset_id,
                    MemoryAssetModel.user_id == user_id,
                )
            )
            if model is None:
                return None
            for field in (
                "extracted_text",
                "extraction_status",
                "encoder_name",
                "embedding_dimension",
                "index_status",
                "asset_metadata",
            ):
                if field in changes:
                    setattr(model, field, changes[field])
            model.updated_at = _utcnow()
            await session.commit()
            await session.refresh(model)
            return _asset(model)

    async def save_graph_projection(
        self,
        memory: MemoryRecord,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        sync_status: str = "pending",
        last_error: str | None = None,
    ) -> None:
        """保存 Neo4j 投影的重建输入。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(MemoryGraphProjectionModel).where(
                    MemoryGraphProjectionModel.memory_id == memory.memory_id
                )
            )
            if model is None:
                model = MemoryGraphProjectionModel(
                    projection_id=str(uuid4()), memory_id=memory.memory_id
                )
                session.add(model)
            model.entities = entities
            model.relations = relations
            model.sync_status = sync_status
            model.last_error = last_error
            model.updated_at = _utcnow()
            await session.commit()

    async def get_graph_projection(self, memory_id: str) -> dict[str, Any] | None:
        """读取 Neo4j 投影的可重建输入。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(MemoryGraphProjectionModel).where(
                    MemoryGraphProjectionModel.memory_id == memory_id
                )
            )
            if model is None:
                return None
            return {
                "memory_id": model.memory_id,
                "entities": list(model.entities or []),
                "relations": list(model.relations or []),
                "sync_status": model.sync_status,
                "last_error": model.last_error,
            }

    async def list_graph_projections(
        self, *, sync_status: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """列出待同步图投影，便于 Neo4j 后启用时批量重建。"""
        async with self.session_factory() as session:
            models = (
                await session.scalars(
                    select(MemoryGraphProjectionModel)
                    .where(MemoryGraphProjectionModel.sync_status == sync_status)
                    .order_by(MemoryGraphProjectionModel.created_at)
                    .limit(max(0, limit))
                )
            ).all()
        return [
            {
                "memory_id": model.memory_id,
                "entities": list(model.entities or []),
                "relations": list(model.relations or []),
                "sync_status": model.sync_status,
                "last_error": model.last_error,
            }
            for model in models
        ]

    async def update_graph_projection(
        self,
        memory_id: str,
        sync_status: str,
        error: str = "",
    ) -> None:
        """更新 Neo4j 投影的同步状态。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(MemoryGraphProjectionModel).where(
                    MemoryGraphProjectionModel.memory_id == memory_id
                )
            )
            if model is None:
                return
            model.sync_status = sync_status
            model.last_error = error[:4000] or None
            model.updated_at = _utcnow()
            await session.commit()

    async def enqueue_index_job(
        self, memory_id: str, target: str, error: str = ""
    ) -> None:
        """写入可重试索引任务；同一记忆和目标只保留一个未完成任务。"""
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(MemoryIndexJobModel).where(
                    MemoryIndexJobModel.memory_id == memory_id,
                    MemoryIndexJobModel.target == target,
                    MemoryIndexJobModel.status.in_(("pending", "processing", "failed")),
                )
            )
            if existing:
                if existing.status == "failed":
                    existing.status = "pending"
                existing.last_error = error[:4000] or None
                existing.updated_at = _utcnow()
            else:
                session.add(
                    MemoryIndexJobModel(
                        job_id=str(uuid4()),
                        memory_id=memory_id,
                        target=target,
                        status="pending",
                        last_error=error[:4000] or None,
                    )
                )
            await session.commit()

    async def list_index_jobs(
        self, limit: int = 100, max_attempts: int = 5
    ) -> list[dict[str, Any]]:
        """返回待处理和失败任务，供后台同步器消费。"""
        async with self.session_factory() as session:
            models = (
                await session.scalars(
                    select(MemoryIndexJobModel)
                    .where(
                        MemoryIndexJobModel.status.in_(("pending", "failed")),
                        MemoryIndexJobModel.attempts < max(1, max_attempts),
                    )
                    .order_by(MemoryIndexJobModel.created_at)
                    .limit(max(0, limit))
                )
            ).all()
        return [
            {
                "job_id": model.job_id,
                "memory_id": model.memory_id,
                "target": model.target,
                "status": model.status,
                "attempts": model.attempts,
                "last_error": model.last_error,
            }
            for model in models
        ]

    async def update_index_job(self, job_id: str, status: str, error: str = "") -> None:
        """更新索引任务状态和重试次数。"""
        async with self.session_factory() as session:
            model = await session.get(MemoryIndexJobModel, job_id)
            if model is None:
                return
            model.status = status
            model.last_error = error[:4000] or None
            model.updated_at = _utcnow()
            if status == "processing":
                model.attempts += 1
            await session.commit()
