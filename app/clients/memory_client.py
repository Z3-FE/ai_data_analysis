"""应用级 Memory Runtime 生命周期管理。"""

import logging

from app.agent.memory.factory import MemoryRuntime, build_memory_runtime
from app.clients.embedding_client import embedding_client_manager
from app.clients.llm_client import llm_client_manager
from app.clients.neo4j_client import neo4j_client_manager
from app.clients.postgres_client import postgres_client_manager
from app.clients.qdrant_client import qdrant_client_manager

logger = logging.getLogger(__name__)


class MemoryClientManager:
    """组装并持有当前应用进程复用的 Memory Runtime。"""

    def __init__(self) -> None:
        # Memory Runtime 依赖 PostgreSQL、Qdrant、Neo4j 和 LLM 的已初始化客户端。
        self.runtime: MemoryRuntime | None = None

    async def init(self) -> None:
        """使用已初始化的基础设施组装记忆层。"""
        if self.runtime is not None:
            return
        session_factory = postgres_client_manager.session_factory
        if session_factory is None:
            raise RuntimeError("Memory Runtime 需要先初始化 PostgreSQL 客户端")

        self.runtime = await build_memory_runtime(
            session_factory=session_factory,
            qdrant_client=qdrant_client_manager.client,
            embedding_client=embedding_client_manager.client,
            neo4j_driver=neo4j_client_manager.driver,
            llm_client=llm_client_manager.client,
        )
        logger.info(
            "Memory Runtime 初始化完成：qdrant=%s neo4j=%s formation=%s",
            self.runtime.vector_enabled,
            self.runtime.graph_enabled,
            self.runtime.formation_service is not None,
        )

    async def close(self) -> None:
        """等待记忆形成后台任务结束并释放 Runtime 引用。"""
        if self.runtime is not None and self.runtime.formation_service is not None:
            await self.runtime.formation_service.close()
        self.runtime = None


memory_client_manager = MemoryClientManager()


__all__ = ["MemoryClientManager", "memory_client_manager"]
