"""ContextEngine 会话摘要和构建审计 ORM 模型。"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ContextConversationSummaryModel(Base):
    """一个会话持续增量更新的较早历史摘要。"""

    __tablename__ = "context_conversation_summaries"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "conversation_id",
            name="uk_context_summary_user_conversation",
        ),
        Index(
            "idx_context_summaries_user_updated",
            "user_id",
            "updated_at",
        ),
        {"comment": "ContextEngine 会话增量摘要"},
    )

    # 摘要稳定 ID；后续更新同一行而不是每轮复制一份摘要。
    summary_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 摘要所属用户，读取时必须与会话 ID 同时校验。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 摘要所属业务会话 ID。
    conversation_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 已压缩的较早会话语义，不保存隐藏思考。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 摘要覆盖的第一条 Working Message 序号。
    covered_from_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 摘要覆盖的最后一条 Working Message 序号。
    covered_through_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # 当前摘要累计覆盖的原始消息数量。
    source_message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 摘要正文使用当前 tokenizer 计算出的 token 数。
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 摘要增量更新版本；数据库写入时递增。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 首次形成摘要的时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 最近一次扩展覆盖范围的时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ContextBuildRunModel(Base):
    """一次上下文构建的紧凑审计，不复制问题或候选正文。"""

    __tablename__ = "context_build_runs"
    __table_args__ = (
        Index(
            "idx_context_builds_user_conversation",
            "user_id",
            "conversation_id",
            "created_at",
        ),
        Index("idx_context_builds_status_created", "status", "created_at"),
        {"comment": "ContextEngine 构建审计"},
    )

    # 单次上下文构建 ID。
    build_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 构建所属用户，用于审计隔离。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 构建所属会话。
    conversation_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 使用上下文的 Agent 类型，例如 data_agent 或 decision_agent。
    agent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # pending/completed/failed。
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # 原始问题 SHA-256，只用于关联审计，不保存原问题。
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 本轮允许的最大上下文 token 数。
    token_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    # 最终编译消息的真实 token 数。
    final_token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Gather 阶段候选总数。
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 最终进入模型上下文的候选数。
    selected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 检索来源、开关和原因，不包含 search_query。
    retrieval_plan: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 历史依赖、附件 ID 和未解析引用。
    reference_resolution: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # 候选 ID、分数、token、处理原因和来源 ID，不包含正文。
    decisions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 本轮是否扩展了持久化会话摘要。
    summary_updated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # 构建失败说明；成功时为空。
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 开始构建的时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 构建完成或失败的时间。
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


__all__ = ["ContextBuildRunModel", "ContextConversationSummaryModel"]
