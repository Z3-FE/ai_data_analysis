"""长期记忆相关的 PostgreSQL ORM 模型。

PostgreSQL 是记忆的事实来源；Qdrant 和 Neo4j 只保存可以删除、重建的检索
投影。这里不把记忆直接混入 conversation_messages，因为一条消息和从消息中
提炼出的长期事实具有不同的生命周期。
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentMemoryModel(Base):
    """长期记忆事实、任务经验或感知内容的主记录。"""

    __tablename__ = "agent_memories"
    __table_args__ = (
        Index(
            "idx_agent_memories_user_type_status", "user_id", "memory_type", "status"
        ),
        Index("idx_agent_memories_conversation", "conversation_id"),
        Index("idx_agent_memories_project", "project_id"),
        Index("idx_agent_memories_supersedes", "supersedes_memory_id"),
        Index("idx_agent_memories_expires", "expires_at"),
        {"comment": "Agent 长期记忆事实主表"},
    )

    # 记忆记录的稳定唯一 ID，也是 Qdrant point_id 的来源。
    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 记忆所属用户；所有跨会话检索必须按此字段隔离。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 记忆所属类型：episodic、semantic 或 perceptual。
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 记忆所属范围，例如 user、conversation 或 project。
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    # 产生记忆的会话 ID；跨会话用户记忆可以为空。
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 项目级记忆所属项目 ID；用户级和会话级记忆为空。
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 记忆可供检索和展示的正文。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 供过滤、图投影和业务扩展使用的结构化数据。
    structured_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 记忆当前生命周期状态：active、superseded、forgotten 或 expired。
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    # 记忆重要性，范围约定为 0 到 1，用于排序和保留策略。
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # 记忆提取或人工确认的可信度，范围约定为 0 到 1。
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # 同一逻辑记忆的版本号，新版本递增。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 当前记录替代的旧记忆 ID，用于保留可追溯的版本链。
    supersedes_memory_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 记忆过期时间；为空表示没有预设过期时间。
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 记忆被检索命中的次数，用于访问统计和后续巩固策略。
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 最近一次被检索命中的时间。
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 首次创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 最近一次内容或状态更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class MemorySourceModel(Base):
    """记录一条记忆来自哪个业务对象。"""

    __tablename__ = "memory_sources"
    __table_args__ = (
        UniqueConstraint(
            "memory_id", "source_type", "source_id", name="uk_memory_source"
        ),
        Index("idx_memory_sources_memory", "memory_id"),
        {"comment": "记忆来源关联"},
    )

    # 来源关联记录的唯一 ID。
    source_link_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 被引用的记忆 ID。
    memory_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_memories.memory_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 来源对象类型：message、turn、output 或 asset。
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 来源对象在对应业务表中的 ID。
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 可选的来源路径，例如 payload.result_summary。
    source_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 该来源关联的创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class MemoryAssetModel(Base):
    """附件和多模态输入的统一元数据表。"""

    __tablename__ = "memory_assets"
    __table_args__ = (
        Index("idx_memory_assets_user_conversation", "user_id", "conversation_id"),
        Index("idx_memory_assets_project", "project_id"),
        Index(
            "idx_memory_assets_modality_status",
            "modality",
            "extraction_status",
            "index_status",
        ),
        {"comment": "记忆层附件元数据"},
    )

    # 附件的稳定唯一 ID，后续会作为历史引用的对象 ID。
    asset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 上传附件的用户 ID。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 附件所属会话 ID。
    conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 项目级附件所属项目 ID；项目外的附件为空。
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 当前附件模态：text、image、audio 或 video。
    modality: Mapped[str] = mapped_column(String(32), nullable=False)
    # 用户上传时的原始文件名。
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # 文件 MIME 类型。
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # 文件实际存储地址；当前可以是本地路径或对象存储 URI。
    storage_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    # 从附件提取出的可检索文本；图片和音频支持后再填充。
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 附件内容提取状态：pending、completed、failed 或 unsupported。
    extraction_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    # 生成向量时使用的编码器名称；未建立索引时为空。
    encoder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 向量维度，便于重建索引时检查模型是否一致。
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Qdrant 索引同步状态：pending、completed 或 failed。
    index_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    # 附件扩展元数据，例如大小、校验和、页面数等。
    asset_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    # 附件记录创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 附件记录最近更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class MemoryGraphProjectionModel(Base):
    """Neo4j 图投影的 PostgreSQL 可重建记录。"""

    __tablename__ = "memory_graph_projections"
    __table_args__ = (
        Index("idx_memory_graph_projections_status", "sync_status"),
        {"comment": "Semantic Memory 图投影事实记录"},
    )

    # 图投影记录的稳定唯一 ID。
    projection_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 对应的 Semantic Memory ID。
    memory_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_memories.memory_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # 结构化实体列表，作为 Neo4j 重建输入。
    entities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 结构化关系列表，作为 Neo4j 重建输入。
    relations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 图投影当前同步状态：pending、completed 或 failed。
    sync_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    # 最近一次同步错误信息。
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 图投影记录创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 图投影记录最近更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class MemoryIndexJobModel(Base):
    """Qdrant/Neo4j 索引同步任务表。"""

    __tablename__ = "memory_index_jobs"
    __table_args__ = (
        Index("idx_memory_index_jobs_status", "status", "target", "created_at"),
        Index(
            "uk_memory_index_jobs_active",
            "memory_id",
            "target",
            unique=True,
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
        {"comment": "记忆检索索引同步任务"},
    )

    # 索引任务的稳定唯一 ID。
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 待同步的记忆 ID。
    memory_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("agent_memories.memory_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 索引目标：qdrant 或 neo4j。
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    # 当前任务状态：pending、processing、completed 或 failed。
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # 当前已重试次数。
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 最近一次执行错误。
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 任务创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 任务最近更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


__all__ = [
    "AgentMemoryModel",
    "MemorySourceModel",
    "MemoryAssetModel",
    "MemoryGraphProjectionModel",
    "MemoryIndexJobModel",
]
