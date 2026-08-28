"""应用会话历史 ORM 模型。

这些表属于独立的 ``agent_app`` 数据库，只保存会话恢复和前端重新渲染所需的
最小数据，不与 DW、Meta 的业务数据表混用。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConversationModel(Base):
    """一个用户可持续访问的聊天会话。"""

    __tablename__ = "conversations"
    __table_args__ = {"comment": "Agent 应用会话"}

    conversation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="新建会话")
    data_source_id: Mapped[str] = mapped_column(String(128), nullable=False, default="olist")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    active_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    conversation_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ConversationTurnModel(Base):
    """一次用户提问及其 Agent 运行结果。"""

    __tablename__ = "conversation_turns"
    __table_args__ = {"comment": "Agent 会话轮次"}

    turn_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class ConversationMessageModel(Base):
    """会话中可直接展示的用户或助手消息。"""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("turn_id", "sequence_no", name="uk_message_turn_sequence"),
        {"comment": "Agent 会话消息"},
    )

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_turns.turn_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )


class TurnOutputModel(Base):
    """一轮可供前端恢复渲染的结构化输出。"""

    __tablename__ = "turn_outputs"
    __table_args__ = (
        UniqueConstraint("turn_id", "output_type", name="uk_output_turn_type"),
        {"comment": "Agent 轮次可渲染输出"},
    )

    output_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("conversation_turns.turn_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    output_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


__all__ = [
    "ConversationModel",
    "ConversationTurnModel",
    "ConversationMessageModel",
    "TurnOutputModel",
]
