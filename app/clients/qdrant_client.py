"""Qdrant 异步客户端管理器。

这里只负责创建、保存和关闭 AsyncQdrantClient，不承载集合创建、写入、查询或过滤逻辑。
具体 Qdrant 操作统一放到 repositories/qdrant 下。
"""

from typing import Optional

from qdrant_client import AsyncQdrantClient

from app.core.config import QdrantConfig, settings


class QdrantClientManager:
    """管理 Qdrant 异步客户端的生命周期。"""

    def __init__(self, config: QdrantConfig) -> None:
        self.config = config
        self.client: Optional[AsyncQdrantClient] = None

    def init(self) -> None:
        """显式初始化 Qdrant 异步客户端。"""
        self.client = AsyncQdrantClient(
            url=self.config.url,
            check_compatibility=False,
        )

    async def close(self) -> None:
        """关闭 Qdrant 异步客户端。"""
        if self.client is not None:
            await self.client.close()
            self.client = None


qdrant_client_manager = QdrantClientManager(settings.qdrant)
