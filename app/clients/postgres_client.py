"""Agent 平台 PostgreSQL 客户端和 LangGraph Checkpointer 管理器。"""

from contextlib import AbstractAsyncContextManager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.agent.graph import build_agent_graph
from app.core.config import PostgresConfig, settings
from app.models.agent_history import (
    ConversationMessageModel,
    ConversationModel,
    ConversationTurnModel,
    TurnOutputModel,
)
from app.models.base import Base

# 只允许会话历史模型参与 agent_app 建表，避免把 MySQL Meta ORM 误创建到这里。
_AGENT_HISTORY_MODELS = (
    ConversationModel,
    ConversationTurnModel,
    ConversationMessageModel,
    TurnOutputModel,
)


class PostgresClientManager:
    """管理会话历史数据库和官方 LangGraph Checkpointer。"""

    def __init__(self, config: PostgresConfig) -> None:
        self.config = config
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker | None = None
        self.checkpointer: AsyncPostgresSaver | None = None
        self.agent_graph: Any | None = None
        self._checkpointer_context: AbstractAsyncContextManager | None = None

    async def init(self) -> None:
        """初始化业务表、官方 Checkpointer 和持久化 Agent 图。"""
        if self.agent_graph is not None:
            return

        self.engine = create_async_engine(
            self.config.database_url,
            pool_pre_ping=self.config.pool_pre_ping,
            pool_recycle=self.config.pool_recycle,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            autoflush=True,
            expire_on_commit=False,
        )

        try:
            await self._create_business_tables()
            self._checkpointer_context = AsyncPostgresSaver.from_conn_string(
                self.config.checkpointer_url
            )
            self.checkpointer = await self._checkpointer_context.__aenter__()
            await self.checkpointer.setup()
            self.agent_graph = build_agent_graph(checkpointer=self.checkpointer)
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
                tables=[model.__table__ for model in _AGENT_HISTORY_MODELS],
            )

    async def close(self) -> None:
        """释放 Checkpointer 和 SQLAlchemy 连接池。"""
        if self._checkpointer_context is not None:
            await self._checkpointer_context.__aexit__(None, None, None)
            self._checkpointer_context = None
        self.checkpointer = None
        self.agent_graph = None
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None


postgres_client_manager = PostgresClientManager(settings.postgres)


__all__ = ["PostgresClientManager", "postgres_client_manager"]
