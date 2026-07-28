"""Elasticsearch 连接客户端。

这里只负责创建 Elasticsearch 实例和基础健康检查，不承载索引创建、写入、
alias 切换或搜索逻辑。具体业务操作统一下放到
`app.repositories.elasticsearch_repository`。
"""

from elasticsearch import Elasticsearch

from app.core.config import settings


class FullTextSearchClient:
    """Elasticsearch 连接包装。"""

    def __init__(self, url: str | None = None) -> None:
        self.client = Elasticsearch(url or settings.elasticsearch.url)

    def health(self) -> bool:
        """检查 Elasticsearch 服务是否可访问。"""
        return bool(self.client.ping())
