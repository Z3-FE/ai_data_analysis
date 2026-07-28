"""Qdrant 连接客户端。

这里只负责创建 QdrantClient 实例和基础健康检查，不承载任何集合创建、写入、
查询或过滤逻辑。具体业务操作统一下放到 `app.repositories.qdrant_repository`。
"""

from qdrant_client import QdrantClient

from app.core.config import settings


class VectorDbClient:
    """Qdrant 连接包装。"""

    def __init__(self, url: str | None = None) -> None:
        self.client = QdrantClient(
            url=url or settings.qdrant.url,
            check_compatibility=False,
        )

    def health(self) -> bool:
        """检查 Qdrant 服务是否可访问。"""
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False
