"""Elasticsearch 异步客户端管理器。

这里只负责创建、保存和关闭 AsyncElasticsearch，不承载索引创建、写入、alias
切换或搜索逻辑。具体业务操作统一下放到 repository。
"""

from typing import Optional

from elasticsearch import AsyncElasticsearch

from app.core.config import ElasticsearchConfig, settings


class ElasticsearchClientManager:
    """管理 Elasticsearch 异步客户端的初始化与关闭。"""

    def __init__(self, config: ElasticsearchConfig) -> None:
        self.config = config
        self.client: Optional[AsyncElasticsearch] = None

    def init(self) -> None:
        """显式初始化 Elasticsearch 异步客户端。"""
        self.client = AsyncElasticsearch(self.config.url)

    async def close(self) -> None:
        """关闭 Elasticsearch 异步客户端。"""
        if self.client is not None:
            await self.client.close()
            self.client = None


elasticsearch_client_manager = ElasticsearchClientManager(settings.elasticsearch)
