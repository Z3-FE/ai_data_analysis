"""Qdrant 通用仓库。

当前项目在线 Agent 检索优先使用 repositories/qdrant 下的专用异步仓库。
这个通用仓库主要服务离线向量构建脚本，暂时保留同步方法签名。
"""

import asyncio
from typing import Any, Awaitable, TypeVar

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

_DISTANCE_MAP = {
    "Cosine": Distance.COSINE,
    "Dot": Distance.DOT,
    "Euclid": Distance.EUCLID,
    "Manhattan": Distance.MANHATTAN,
}

T = TypeVar("T")


def _run_async(awaitable: Awaitable[T]) -> T:
    """在同步构建脚本中执行异步 Qdrant SDK 调用。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("同步 QdrantRepository 不能在已经运行的事件循环中调用")


class QdrantRepository:
    """封装离线构建场景中的 Qdrant 集合、点写入和检索操作。"""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self.client = client

    def collection_exists(self, collection_name: str) -> bool:
        """判断 collection 是否已经存在。"""
        return _run_async(self.client.collection_exists(collection_name))

    def create_collection_if_not_exists(
        self,
        collection_name: str,
        vector_size: int,
        distance: str,
        payload_indexes: tuple[str, ...] = (),
    ) -> None:
        """仅在 collection 不存在时创建，用于支持断点续建。"""
        if not self.collection_exists(collection_name):
            _run_async(
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=vector_size,
                        distance=_DISTANCE_MAP[distance],
                    ),
                )
            )

        for field_name in payload_indexes:
            try:
                _run_async(
                    self.client.create_payload_index(
                        collection_name=collection_name,
                        field_name=field_name,
                        field_schema=PayloadSchemaType.KEYWORD,
                        wait=True,
                    )
                )
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise

    def recreate_collection(
        self,
        collection_name: str,
        vector_size: int,
        distance: str,
        payload_indexes: tuple[str, ...] = (),
    ) -> None:
        """删除并重建 collection，保证重复构建时没有旧数据残留。"""
        if self.collection_exists(collection_name):
            _run_async(self.client.delete_collection(collection_name))

        self.create_collection_if_not_exists(
            collection_name=collection_name,
            vector_size=vector_size,
            distance=distance,
            payload_indexes=payload_indexes,
        )

    def upsert_points(self, collection_name: str, points: list[PointStruct]) -> None:
        """批量写入 Qdrant points。"""
        if not points:
            return
        from app.core.config import settings

        batch_size = settings.qdrant.upsert_batch_size
        for start in range(0, len(points), batch_size):
            _run_async(
                self.client.upsert(
                    collection_name=collection_name,
                    points=points[start : start + batch_size],
                    wait=True,
                )
            )

    def get_collection_count(self, collection_name: str) -> int:
        """返回 collection 中的 point 数量。"""
        result = _run_async(self.client.count(collection_name=collection_name, exact=True))
        return result.count

    def point_exists(self, collection_name: str, point_id: str) -> bool:
        """判断指定 point 是否已经写入。"""
        records = _run_async(
            self.client.retrieve(
                collection_name=collection_name,
                ids=[point_id],
                with_payload=False,
                with_vectors=False,
            )
        )
        return bool(records)

    def search_points(
        self,
        collection_name: str,
        vector: list[float],
        limit: int,
        score_threshold: float | None = None,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        """按向量检索启用状态的 points，并返回字典。"""
        response = _run_async(
            self.client.query_points(
                collection_name=collection_name,
                query=vector,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="status",
                            match=MatchValue(value=status),
                        )
                    ]
                ),
                limit=limit,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
        )
        return [
            {
                "id": str(point.id),
                "score": point.score,
                "payload": dict(point.payload or {}),
            }
            for point in response.points
        ]
