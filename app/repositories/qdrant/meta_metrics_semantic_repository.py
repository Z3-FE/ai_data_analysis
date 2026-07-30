"""`meta_metrics_semantic` 指标语义向量仓储。"""

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.core.config import settings
from app.entities.qdrant import MetaMetricsSemantic, MetaMetricsSemanticPayload


class MetaMetricsSemanticRepository:
    """负责 meta_metrics_semantic 集合的指标语义检索。"""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self.client = client
        self.collection_name = settings.qdrant.metrics_collection

    async def search(
        self,
        vector: list[float],
        score_threshold: float = 0.55,
        limit: int = 5,
        status: str = "active",
    ) -> list[MetaMetricsSemantic]:
        """按向量相似度检索指标元数据，并转变为 MetaMetricsSemantic 实体。"""
        response = await self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=Filter(
                must=[FieldCondition(key="status", match=MatchValue(value=status))]
            ),
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
            with_vectors=False,
        )
        return [
            MetaMetricsSemantic(
                id=str(point.id),
                score=float(point.score),
                payload=MetaMetricsSemanticPayload(**dict(point.payload or {})),
            )
            for point in response.points
        ]
