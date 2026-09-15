"""PostgreSQL 记忆事实仓储。"""

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, desc, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory.enums import (
    MemoryDecisionAction,
    MemoryFormationStatus,
    MemoryFormationTrigger,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryCreate,
    MemoryFormationRecord,
    MemoryRecord,
    MemorySource,
    MemoryWriteResult,
)
from app.agent.memory.lexical import lexical_scores
from app.agent.memory.write_policy import resolve_memory_write_identity
from app.models.agent_history import (
    ConversationMessageModel,
    ConversationTurnModel,
    TurnOutputModel,
)
from app.models.memory import (
    AgentMemoryModel,
    MemoryAssetModel,
    MemoryFormationRunModel,
    MemoryGraphProjectionModel,
    MemoryIndexJobModel,
    MemorySourceModel,
)


def _utcnow() -> datetime:
    """返回无时区 UTC，匹配当前数据库表的 TIMESTAMP 类型。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_safe(value: Any) -> Any:
    """把 Decimal 等数据库返回类型转换成 JSONB 可保存的值。"""
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


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


def _formation_record(model: MemoryFormationRunModel) -> MemoryFormationRecord:
    """把形成审计 ORM 记录转换为领域快照。"""
    return MemoryFormationRecord(
        formation_run_id=model.formation_run_id,
        formation_key=model.formation_key,
        status=MemoryFormationStatus(model.status),
        trigger=MemoryFormationTrigger(model.trigger),
        candidate_count=model.candidate_count,
        accepted_count=model.accepted_count,
        rejected_count=model.rejected_count,
        duplicate_count=model.duplicate_count,
        replaced_count=model.replaced_count,
        failed_count=model.failed_count,
        decisions=list(model.decisions or []),
        error_message=model.error_message or "",
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
    if request.scope is MemoryScope.CONVERSATION and not request.conversation_id:
        raise ValueError("conversation 作用域的记忆必须提供 conversation_id")
    if request.scope is MemoryScope.PROJECT and not request.project_id:
        raise ValueError("project 作用域的记忆必须提供 project_id")
    if request.scope is not MemoryScope.PROJECT and request.project_id:
        raise ValueError("只有 project 作用域的记忆可以绑定 project_id")


def _new_memory_model(
    request: MemoryCreate,
    *,
    version: int = 1,
    supersedes_memory_id: str | None = None,
) -> AgentMemoryModel:
    """把治理后的请求转换为 ORM 对象，版本身份只由仓储生成。"""
    now = _utcnow()
    return AgentMemoryModel(
        memory_id=str(uuid4()),
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
        version=version,
        supersedes_memory_id=supersedes_memory_id,
        expires_at=request.expires_at,
        created_at=now,
        updated_at=now,
    )


async def _add_sources_idempotent(
    session: AsyncSession, memory_id: str, sources: tuple[MemorySource, ...]
) -> None:
    """在当前事务中幂等追加来源，供重复和并发写入路径复用。"""
    if not sources:
        return
    await session.execute(
        insert(MemorySourceModel)
        .values(
            [
                {
                    "source_link_id": str(uuid4()),
                    "memory_id": memory_id,
                    "source_type": source.source_type,
                    "source_id": source.source_id,
                    "source_path": source.source_path,
                }
                for source in sources
            ]
        )
        .on_conflict_do_nothing(
            index_elements=["memory_id", "source_type", "source_id"]
        )
    )


def _scope_conditions(request: MemoryCreate) -> list[Any]:
    """构造精确作用域条件，避免用户级记忆误命中会话或项目记忆。"""
    return [
        AgentMemoryModel.scope == request.scope.value,
        (
            AgentMemoryModel.conversation_id == request.conversation_id
            if request.conversation_id is not None
            else AgentMemoryModel.conversation_id.is_(None)
        ),
        (
            AgentMemoryModel.project_id == request.project_id
            if request.project_id is not None
            else AgentMemoryModel.project_id.is_(None)
        ),
    ]


def _write_lock_identity(request: MemoryCreate) -> str:
    """根据记忆类型生成事务锁身份，避免并发重复写入。"""
    memory_identity = resolve_memory_write_identity(request)
    identity = {
        "user_id": request.user_id.strip(),
        "memory_type": request.memory_type.value,
        "scope": request.scope.value,
        "conversation_id": request.conversation_id,
        "project_id": request.project_id,
        "memory_identity": {
            "type": memory_identity.identity_type,
            "key": memory_identity.identity_key,
            "qualifiers": memory_identity.qualifiers,
        },
    }
    return json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str)


def _identity_conditions(request: MemoryCreate) -> list[Any]:
    """构造类型化身份的数据库预筛选条件。"""
    identity = resolve_memory_write_identity(request)
    identity_type = identity.identity_type
    if identity_type == "semantic_fact":
        return [
            AgentMemoryModel.structured_data["fact_key"].as_string()
            == identity.identity_key
        ]
    if identity_type == "episodic_event":
        return [
            AgentMemoryModel.structured_data["event_key"].as_string()
            == identity.identity_key
        ]
    if identity_type == "perceptual_asset":
        return [
            AgentMemoryModel.structured_data["asset_id"].as_string()
            == identity.identity_key,
            AgentMemoryModel.structured_data["modality"].as_string()
            == identity.qualifiers["modality"],
        ]
    return [AgentMemoryModel.content == request.content.strip()]


def _same_type_identity(model: AgentMemoryModel, request: MemoryCreate) -> bool:
    """复核 JSON 条件，避免语义事实在不同适用条件间互相替换。"""
    identity = resolve_memory_write_identity(request)
    if identity.identity_type != "semantic_fact":
        return True
    model_data = model.structured_data if isinstance(model.structured_data, dict) else {}
    model_conditions = model_data.get("conditions", {})
    return model_conditions == identity.qualifiers["conditions"]


def _same_payload(model: AgentMemoryModel, request: MemoryCreate) -> bool:
    """比较会改变记忆语义的正文和结构化内容。"""
    model_data = model.structured_data if isinstance(model.structured_data, dict) else {}
    return (
        model.content == request.content.strip()
        and _json_safe(model_data) == _json_safe(request.structured_data)
    )


class PostgresMemoryRepository:
    """负责记忆事实、来源、附件和同步任务的 PostgreSQL 读写。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        # 由应用级 PostgreSQL 客户端注入，和会话历史共用连接池但不共用业务表。
        self.session_factory = session_factory

    async def write_managed(self, request: MemoryCreate) -> MemoryWriteResult:
        """按记忆类型在同一事务内完成幂等创建或版本替换。"""
        _validate_create(request)

        async with self.session_factory() as session:
            async with session.begin():
                # 相同逻辑事实共享一把事务级锁。事务提交或回滚后锁自动释放。
                await session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended(:lock_identity, 0))"
                    ),
                    {"lock_identity": _write_lock_identity(request)},
                )

                conditions = [
                    AgentMemoryModel.user_id == request.user_id.strip(),
                    AgentMemoryModel.memory_type == request.memory_type.value,
                    AgentMemoryModel.status == MemoryStatus.ACTIVE.value,
                    or_(
                        AgentMemoryModel.expires_at.is_(None),
                        AgentMemoryModel.expires_at > _utcnow(),
                    ),
                    *_scope_conditions(request),
                    *_identity_conditions(request),
                ]
                active_models = list(
                    (
                        await session.scalars(
                            select(AgentMemoryModel)
                            .where(and_(*conditions))
                            .order_by(desc(AgentMemoryModel.version))
                            .with_for_update()
                        )
                    ).all()
                )
                active_models = [
                    model
                    for model in active_models
                    if _same_type_identity(model, request)
                ]

                current = active_models[0] if active_models else None
                if current is not None and _same_payload(current, request):
                    await _add_sources_idempotent(
                        session, current.memory_id, request.sources
                    )
                    return MemoryWriteResult(
                        action=MemoryDecisionAction.DUPLICATE,
                        record=_record(current),
                    )

                if current is None:
                    new_model = _new_memory_model(request)
                    session.add(new_model)
                    await session.flush()
                    await _add_sources_idempotent(
                        session, new_model.memory_id, request.sources
                    )
                    return MemoryWriteResult(
                        action=MemoryDecisionAction.CREATED,
                        record=_record(new_model),
                    )

                old_record = _record(current)
                new_model = _new_memory_model(
                    request,
                    version=current.version + 1,
                    supersedes_memory_id=current.memory_id,
                )
                current.status = MemoryStatus.SUPERSEDED.value
                current.updated_at = _utcnow()
                session.add(new_model)
                await session.flush()
                await _add_sources_idempotent(
                    session, new_model.memory_id, request.sources
                )
                return MemoryWriteResult(
                    action=MemoryDecisionAction.REPLACED,
                    record=_record(new_model),
                    replaced_record=old_record,
                )

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
        asset_ids: list[str] | None = None,
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
            if asset_ids is not None:
                if not asset_ids:
                    return []
                conditions.append(
                    AgentMemoryModel.structured_data["asset_id"].as_string().in_(
                        list(dict.fromkeys(asset_ids))
                    )
                )
            models = (
                await session.scalars(
                    select(AgentMemoryModel)
                    .where(and_(*conditions))
                    .order_by(desc(AgentMemoryModel.updated_at))
                    .limit(max(100, limit * 20))
                )
            ).all()
        similarities = lexical_scores(query, [model.content for model in models])
        scored: list[tuple[MemoryRecord, float]] = []
        for model, score in zip(models, similarities, strict=True):
            if score > 0 or not query.strip() or asset_ids is not None:
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

    async def list_sources(
        self, memory_id: str, user_id: str
    ) -> list[MemorySource]:
        """按记忆和用户读取来源，防止跨用户暴露来源链。"""
        async with self.session_factory() as session:
            rows = await session.execute(
                select(
                    MemorySourceModel.source_type,
                    MemorySourceModel.source_id,
                    MemorySourceModel.source_path,
                )
                .join(
                    AgentMemoryModel,
                    AgentMemoryModel.memory_id == MemorySourceModel.memory_id,
                )
                .where(
                    MemorySourceModel.memory_id == memory_id,
                    AgentMemoryModel.user_id == user_id,
                )
                .order_by(MemorySourceModel.created_at, MemorySourceModel.source_link_id)
            )
            return [
                MemorySource(
                    source_type=source_type,
                    source_id=source_id,
                    source_path=source_path,
                )
                for source_type, source_id, source_path in rows
            ]

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

    async def record_index_failure(
        self, memory_id: str, target: str, error: str = ""
    ) -> None:
        """记录真实投影同步失败；当前不创建或执行后台任务。"""
        if target not in {"qdrant", "neo4j"}:
            raise ValueError(f"不支持的索引目标：{target}")
        async with self.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "SELECT pg_advisory_xact_lock("
                        "hashtextextended(:lock_identity, 0))"
                    ),
                    {"lock_identity": f"memory-index:{memory_id}:{target}"},
                )
                existing = await session.scalar(
                    select(MemoryIndexJobModel)
                    .where(
                        MemoryIndexJobModel.memory_id == memory_id,
                        MemoryIndexJobModel.target == target,
                        MemoryIndexJobModel.status.in_(
                            ("pending", "processing")
                        ),
                    )
                    .order_by(desc(MemoryIndexJobModel.updated_at))
                    .with_for_update()
                )
                if existing is None:
                    existing = await session.scalar(
                        select(MemoryIndexJobModel)
                        .where(
                            MemoryIndexJobModel.memory_id == memory_id,
                            MemoryIndexJobModel.target == target,
                            MemoryIndexJobModel.status == "failed",
                        )
                        .order_by(desc(MemoryIndexJobModel.updated_at))
                        .with_for_update()
                    )
                if existing is not None:
                    # 无论旧记录处于何种状态，本次调用表达的都是一次失败。
                    existing.status = "failed"
                    existing.last_error = error[:4000] or None
                    existing.updated_at = _utcnow()
                else:
                    session.add(
                        MemoryIndexJobModel(
                            job_id=str(uuid4()),
                            memory_id=memory_id,
                            target=target,
                            status="failed",
                            last_error=error[:4000] or None,
                        )
                    )

    async def create_formation_run(self, payload: dict[str, Any]) -> bool:
        """幂等创建一次记忆形成审计记录。"""
        async with self.session_factory() as session:
            values = {
                "formation_run_id": payload["formation_run_id"],
                "formation_key": payload["formation_key"],
                "user_id": payload["user_id"],
                "conversation_id": payload["conversation_id"],
                "turn_id": payload["turn_id"],
                "run_id": payload["run_id"],
                "trigger": str(payload["trigger"]),
                "status": str(payload["status"]),
                "extractor_version": payload.get(
                    "extractor_version", "m3-v1"
                ),
                "eligibility_reason": payload.get("eligibility_reason", ""),
                "candidate_count": int(payload.get("candidate_count", 0)),
                "accepted_count": int(payload.get("accepted_count", 0)),
                "rejected_count": int(payload.get("rejected_count", 0)),
                "duplicate_count": int(payload.get("duplicate_count", 0)),
                "replaced_count": int(payload.get("replaced_count", 0)),
                "failed_count": int(payload.get("failed_count", 0)),
                "attempts": int(payload.get("attempts", 0)),
                "decisions": _json_safe(payload.get("decisions", [])),
                "error_message": payload.get("error_message") or None,
                "started_at": payload.get("started_at"),
                "completed_at": payload.get("completed_at"),
            }
            statement = (
                insert(MemoryFormationRunModel)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=[MemoryFormationRunModel.formation_key]
                )
                .returning(MemoryFormationRunModel.formation_run_id)
            )
            created_id = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            # PostgreSQL 唯一键负责并发认领；返回 False 表示已有请求先认领。
            return created_id is not None

    async def get_formation_run(
        self, *, formation_key: str, user_id: str
    ) -> MemoryFormationRecord | None:
        """按稳定幂等键读取形成审计快照。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(MemoryFormationRunModel).where(
                    MemoryFormationRunModel.formation_key == formation_key,
                    MemoryFormationRunModel.user_id == user_id,
                )
            )
            return _formation_record(model) if model is not None else None

    async def update_formation_run(
        self, formation_run_id: str, **changes: Any
    ) -> None:
        """更新记忆形成审计状态和紧凑决定列表。"""
        allowed = {
            "status",
            "candidate_count",
            "accepted_count",
            "rejected_count",
            "duplicate_count",
            "replaced_count",
            "failed_count",
            "attempts",
            "decisions",
            "error_message",
            "started_at",
            "completed_at",
        }
        async with self.session_factory() as session:
            model = await session.get(MemoryFormationRunModel, formation_run_id)
            if model is None:
                return
            for key, value in changes.items():
                if key not in allowed:
                    continue
                if key == "decisions":
                    value = _json_safe(value)
                if key == "error_message" and value:
                    value = str(value)[:4000]
                setattr(model, key, value)
            model.updated_at = _utcnow()
            await session.commit()

    async def source_exists(
        self,
        *,
        user_id: str,
        source_type: str,
        source_id: str,
        conversation_id: str | None = None,
    ) -> bool:
        """校验候选来源属于当前用户，避免 LLM 伪造来源引用。"""
        async with self.session_factory() as session:
            if source_type == "output":
                # TurnOutputModel 没有 user_id，必须通过所属轮次做用户隔离。
                conditions = [
                    TurnOutputModel.output_id == source_id,
                    ConversationTurnModel.user_id == user_id,
                    TurnOutputModel.turn_id == ConversationTurnModel.turn_id,
                ]
                if conversation_id:
                    conditions.append(
                        TurnOutputModel.conversation_id == conversation_id
                    )
                return (
                    await session.scalar(
                        select(TurnOutputModel)
                        .join(
                            ConversationTurnModel,
                            TurnOutputModel.turn_id == ConversationTurnModel.turn_id,
                        )
                        .where(and_(*conditions))
                    )
                ) is not None

            source_models: dict[str, Any] = {
                "turn": ConversationTurnModel,
                "message": ConversationMessageModel,
                "asset": MemoryAssetModel,
            }
            model = source_models.get(source_type)
            if model is None:
                return False
            identity_field_name = {
                "turn": "turn_id",
                "message": "message_id",
                "asset": "asset_id",
            }[source_type]
            identity_field = getattr(model, identity_field_name)
            conditions = [identity_field == source_id, model.user_id == user_id]
            if conversation_id:
                conditions.append(model.conversation_id == conversation_id)
            return (
                await session.scalar(select(model).where(and_(*conditions)))
            ) is not None
