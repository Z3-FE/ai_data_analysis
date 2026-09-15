"""Data Agent Harness 的 PostgreSQL 运行状态模型。

Harness 状态不属于 LangGraph Checkpointer 的 channel 状态，因此单独保存。
LangGraph 继续负责旧 Agent 图；这些表负责 LoopController 的暂停、恢复和动作
提交协调。
"""

from datetime import datetime

from sqlalchemy import (
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


class HarnessRunModel(Base):
    """一次可暂停、可恢复的 Harness 运行现场。"""

    __tablename__ = "harness_runs"
    __table_args__ = (
        Index("idx_harness_runs_user_status", "user_id", "status"),
        Index("idx_harness_runs_conversation", "conversation_id"),
        {"comment": "Data Agent Harness 运行状态"},
    )

    # 一次 Harness 运行的稳定 ID，也是恢复接口的外部引用。
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 用户隔离字段；恢复和读取都必须校验。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 业务会话 ID。
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # LangGraph 线程 ID；当前项目通常与 conversation_id 相同。
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 当前用户轮次 ID。
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 用户原始问题；恢复时不能被用户覆盖。
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 数据项目范围；允许为空。
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 本轮附件 ID 列表；恢复时沿用原始范围。
    asset_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Harness 生命周期状态。
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Harness 当前控制阶段。
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    # 已提交动作序号，用于恢复后的下一动作发行。
    action_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 工具循环次数。
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Harness 状态版本，用于防止旧请求覆盖新状态。
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 当前等待的确认 ID；运行不等待确认时为空。
    pending_confirmation_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    # 完整 JSON-safe HarnessGraphState，用于恢复运行现场。
    state_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 最近一次状态更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class HarnessActionModel(Base):
    """Harness 已发行动作的持久化协调记录。"""

    __tablename__ = "harness_actions"
    __table_args__ = (
        UniqueConstraint("run_id", "action_seq", name="uk_harness_action_sequence"),
        Index("idx_harness_actions_run_stage", "run_id", "commit_stage"),
        {"comment": "Data Agent Harness 动作提交记录"},
    )

    # 动作记录 ID。
    action_record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 所属 Harness 运行。
    run_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("harness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 服务端发行的单调递增序号。
    action_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # 工具动作 ID；final_answer 和 ask_user 可以为空。
    action_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # 动作类型：tool_call、ask_user 或 final_answer。
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 受控动作 JSON，不保存模型原始输出和隐藏思考。
    action_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 动作内容摘要哈希，用于重复提交幂等和冲突检测。
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # prepared、checkpoint 或 committed。
    commit_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    # 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 最近一次阶段更新时间。
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class HarnessConfirmationModel(Base):
    """等待用户处理的确认请求及其回复。"""

    __tablename__ = "harness_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "action_seq",
            name="uk_harness_confirmation_action",
        ),
        Index("idx_harness_confirmations_run_status", "run_id", "status"),
        Index("idx_harness_confirmations_user_status", "user_id", "status"),
        {"comment": "Data Agent Harness 用户确认"},
    )

    # 服务端生成的确认请求 ID。
    confirmation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # 所属 Harness 运行。
    run_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("harness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 确认请求对应的动作序号。
    action_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # 用户隔离字段。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 会话隔离字段。
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 展示给用户的问题。
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # 缺少条件、歧义或口径冲突等原因。
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # 允许用户补充的条件字段名。
    required_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 可选项列表。
    choices: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # 完整确认请求 JSON，便于审计和恢复原样展示。
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 请求内容哈希，用于重复创建检测。
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    # pending、confirmed 或 rejected。
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # prepared 或 published；只有 published 才能返回给前端。
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    # 用户回复 JSON；pending 时为空。
    reply_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 回复内容哈希，用于重复提交幂等。
    reply_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 过期时间。
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 请求准备时间。
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 请求对前端可见时间。
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 用户处理完成时间。
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HarnessArtifactModel(Base):
    """工具完整结果的受控 PostgreSQL 存储。"""

    __tablename__ = "harness_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "action_id", name="uk_harness_artifact_action"),
        Index("idx_harness_artifacts_user_created", "user_id", "created_at"),
        Index("idx_harness_artifacts_run_created", "run_id", "created_at"),
        {"comment": "Data Agent Harness 工具结果 Artifact"},
    )

    # Artifact 的稳定内容 ID；由完整 JSON payload 的 SHA-256 生成。
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 对外使用的不透明结果引用，不能单独作为读取授权。
    result_ref: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    # 所属 Harness 运行。
    run_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("harness_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    # 用户隔离字段。
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 业务会话隔离字段。
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # LangGraph 线程隔离字段。
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 当前轮次隔离字段。
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # 产生结果的已提交动作 ID。
    action_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # 产生结果的工具规范名称。
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 结果类型，例如 query_result。
    artifact_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # 完整结果内容；只通过 ArtifactStore 读取。
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # payload 的规范 JSON SHA-256。
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # payload 序列化后的 UTF-8 字节大小。
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # 创建时间。
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    # 过期时间；到期后由治理任务删除或归档。
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


__all__ = [
    "HarnessArtifactModel",
    "HarnessActionModel",
    "HarnessConfirmationModel",
    "HarnessRunModel",
]
