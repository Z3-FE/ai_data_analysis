"""Agent 平台 PostgreSQL 客户端管理器。"""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import PostgresConfig, settings
from app.models.agent_history import (
    ConversationMessageModel,
    ConversationModel,
    ConversationTurnModel,
    TurnOutputModel,
)
from app.models.base import Base
from app.models.context_engine import (
    ContextBuildRunModel,
    ContextConversationSummaryModel,
)
from app.models.harness import (
    HarnessArtifactModel,
    HarnessActionModel,
    HarnessConfirmationModel,
    HarnessFinalizationModel,
    HarnessRunModel,
)
from app.models.memory import (
    AgentMemoryModel,
    MemoryAssetModel,
    MemoryFormationRunModel,
    MemoryGraphProjectionModel,
    MemoryIndexJobModel,
    MemorySourceModel,
)

# 只允许 PostgreSQL Agent 平台模型参与建表，避免把 MySQL Meta ORM 误创建到这里。
_AGENT_APP_MODELS = (
    ConversationModel,
    ConversationTurnModel,
    ConversationMessageModel,
    TurnOutputModel,
    AgentMemoryModel,
    MemorySourceModel,
    MemoryAssetModel,
    MemoryGraphProjectionModel,
    MemoryIndexJobModel,
    MemoryFormationRunModel,
    ContextConversationSummaryModel,
    ContextBuildRunModel,
    HarnessRunModel,
    HarnessActionModel,
    HarnessConfirmationModel,
    HarnessArtifactModel,
    HarnessFinalizationModel,
)


def _mark_active_connection_error_as_disconnect(exception_context: Any) -> None:
    """让连接池淘汰仍处于 ACTIVE 状态的 psycopg 连接。"""
    if "can't change 'autocommit' now" in str(
        exception_context.original_exception
    ):
        exception_context.is_disconnect = True


class PostgresClientManager:
    """管理 Harness、会话历史和记忆层共用的 PostgreSQL 连接池。"""

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker | None = None

    async def init(self) -> None:
        """初始化业务表和 PostgreSQL 会话工厂。"""
        if self.session_factory is not None:
            return

        self.engine = create_async_engine(
            self.config.database_url,
            pool_pre_ping=self.config.pool_pre_ping,
            pool_recycle=self.config.pool_recycle,
        )
        event.listen(
            self.engine.sync_engine,
            "handle_error",
            _mark_active_connection_error_as_disconnect,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            autoflush=True,
            expire_on_commit=False,
        )

        try:
            await self._create_business_tables()
        except Exception:
            await self.close()
            raise

    async def _create_business_tables(self) -> None:
        """按 ORM 模型创建 PostgreSQL agent_app 业务表。"""
        if self.engine is None:
            raise RuntimeError("PostgreSQL Engine 尚未初始化")
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[model.__table__ for model in _AGENT_APP_MODELS],
            )

    async def close(self) -> None:
        """释放 SQLAlchemy 连接池。"""
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None


postgres_client_manager = PostgresClientManager(settings.postgres)


__all__ = ["PostgresClientManager", "postgres_client_manager"]
