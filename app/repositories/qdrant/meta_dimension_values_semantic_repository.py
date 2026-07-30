"""`meta_dimension_values_semantic` 维度值语义向量仓储。"""

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.core.config import settings
from app.entities.qdrant import MetaDimensionValuesSemantic, MetaDimensionValuesSemanticPayload


class MetaDimensionValuesSemanticRepository:
    """负责 meta_dimension_values_semantic 集合的维度值语义检索。"""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self.client = client
        self.collection_name = settings.qdrant.dimension_values_collection

    async def search(
        self,
        vector: list[float],
        score_threshold: float = 0.55,
        limit: int = 5,
        status: str = "active",
    ) -> list[MetaDimensionValuesSemantic]:
        """按向量相似度检索维度值元数据，并转变为 MetaDimensionValuesSemantic 实体。"""
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
            MetaDimensionValuesSemantic(
                id=str(point.id),
                score=float(point.score),
                payload=MetaDimensionValuesSemanticPayload(**dict(point.payload or {})),
            )
            for point in response.points
        ]
