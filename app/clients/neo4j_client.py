"""Neo4j 异步客户端生命周期管理。

客户端只负责创建和释放官方 Neo4j driver。Cypher、节点模型和同步策略
分别放在 app/repositories/memory 与 app/agent/memory/graph，
避免基础设施客户端承担记忆业务逻辑。
"""

from neo4j import AsyncDriver, AsyncGraphDatabase

from app.core.config import Neo4jConfig, settings


class Neo4jClientManager:
    """管理 Semantic Memory 使用的 Neo4j 异步 driver。"""

    def __init__(self, config: Neo4jConfig) -> None:
        self.config = config
        self.driver: AsyncDriver | None = None

    def init(self) -> None:
        """创建 driver；真正的网络连接在第一次查询时建立。"""
        if self.driver is None:
            self.driver = AsyncGraphDatabase.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
                max_connection_pool_size=self.config.max_connection_pool_size,
            )

    async def verify_connectivity(self) -> None:
        """主动检查 Neo4j 是否可用，供健康检查或记忆模块启动时调用。"""
        if self.driver is None:
            raise RuntimeError("Neo4j 客户端尚未初始化")
        await self.driver.verify_connectivity()

    async def close(self) -> None:
        """关闭 Neo4j driver 和连接池。"""
        if self.driver is not None:
            await self.driver.close()
            self.driver = None


neo4j_client_manager = Neo4jClientManager(settings.neo4j)


__all__ = ["Neo4jClientManager", "neo4j_client_manager"]
