"""MySQL 客户端管理器。

统一创建和管理项目中的异步 MySQL 客户端。当前项目会同时连接两套 MySQL：
meta 数据库保存结构化元数据，dw 数据库模拟真实数据仓库。
"""

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import MysqlConfig, settings


class MySQLClientManager:
    """管理 MySQL Engine 和 Session 工厂。"""

    def __init__(self, config: MysqlConfig, database: str) -> None:
        self.config = config
        self.database = database
        self.engine: AsyncEngine | None = None
        self.session_factory: async_sessionmaker | None = None

    def _get_url(self) -> str:
        """拼接 MySQL 异步连接地址。"""
        return (
            f"mysql+asyncmy://{self.config.user}:{self.config.password}"
            f"@{self.config.host}:{self.config.port}/{self.database}"
            f"?charset={self.config.charset}"
        )

    def init(self) -> None:
        """初始化 Engine 和 Session 工厂。"""
        self.engine = create_async_engine(
            self._get_url(),
            pool_pre_ping=self.config.pool_pre_ping,
            pool_recycle=self.config.pool_recycle,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            autoflush=True,
            expire_on_commit=False,
        )

    async def close(self) -> None:
        """释放连接池资源。"""
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None


meta_mysql_client_manager = MySQLClientManager(settings.mysql, settings.mysql.meta_database)
dw_mysql_client_manager = MySQLClientManager(settings.mysql, settings.mysql.dw_database)
