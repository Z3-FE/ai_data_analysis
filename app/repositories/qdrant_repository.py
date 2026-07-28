"""Qdrant 仓库。

这一层只负责围绕 Qdrant SDK 实现集合创建、点写入和向量检索等业务操作。
客户端本身只保留连接能力，具体动作都收口到这里。
"""

from collections.abc import Iterable
from typing import Any

from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.clients.qdrant_client import VectorDbClient

_DISTANCE_MAP = {
    "Cosine": Distance.COSINE,
    "Dot": Distance.DOT,
    "Euclid": Distance.EUCLID,
    "Manhattan": Distance.MANHATTAN,
}


class QdrantRepository:
    """封装 Qdrant 的集合、点和检索操作。"""

    def __init__(self, client: VectorDbClient) -> None:
        self.client = client

    @property
    def sdk(self):
        """暴露底层 SDK，便于仓库内部统一调用。"""
        return self.client.client

    def collection_exists(self, collection_name: str) -> bool:
        """判断 collection 是否已经存在。"""
        return self.sdk.collection_exists(collection_name)

    def create_collection_if_not_exists(
        self,
        collection_name: str,
        vector_size: int,
        distance: str,
        payload_indexes: tuple[str, ...] = (),
    ) -> None:
        """仅在 collection 不存在时创建，用于支持断点续建。"""
        if not self.sdk.collection_exists(collection_name):
            self.sdk.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=_DISTANCE_MAP[distance],
                ),
            )

        for field_name in payload_indexes:
            try:
                self.sdk.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                    wait=True,
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
        if self.sdk.collection_exists(collection_name):
            self.sdk.delete_collection(collection_name)

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
            self.sdk.upsert(
                collection_name=collection_name,
                points=points[start : start + batch_size],
                wait=True,
            )

    def get_collection_count(self, collection_name: str) -> int:
        """返回 collection 中的 point 数量。"""
        result = self.sdk.count(collection_name=collection_name, exact=True)
        return result.count

    def point_exists(self, collection_name: str, point_id: str) -> bool:
        """判断指定 point 是否已经写入。"""
        records = self.sdk.retrieve(
            collection_name=collection_name,
            ids=[point_id],
            with_payload=False,
            with_vectors=False,
        )
        return bool(records)

    def search_points(
        self,
        collection_name: str,
        vector: list[float],
        limit: int,
        score_threshold: float | None = None,
        status: str = "active",
    ) -> list[dict]:
        """按向量检索启用状态的 points，并返回便于业务层处理的字典。"""
        response = self.sdk.query_points(
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
        return [
            {
                "id": str(point.id),
                "score": point.score,
                "payload": dict(point.payload or {}),
            }
            for point in response.points
        ]
