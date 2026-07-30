"""维度值的 Elasticsearch + Qdrant 混合检索服务。"""

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.qdrant_repository import QdrantRepository

_EXACT_PRIORITY = {
    "raw_value_exact": 3,
    "display_name_exact": 2,
    "alias_exact": 1,
}


@dataclass
class _MergedCandidate:
    """ES 和 Qdrant 结果融合时使用的内部候选结构。"""

    source: dict[str, Any]
    exact_priority: int = 0
    rrf_score: float = 0.0
    es_score: float | None = None
    vector_score: float | None = None
    match_types: set[str] = field(default_factory=set)


def _candidate_key(source: dict[str, Any]) -> tuple[str, str]:
    """使用字段和值联合去重，避免相同代码在不同字段中被错误合并。"""
    return str(source["column_id"]), str(source["raw_value"])


def _best_exact_priority(matched_queries: list[str]) -> int:
    """返回 ES 命中的最高精确匹配等级。"""
    return max((_EXACT_PRIORITY.get(name, 0) for name in matched_queries), default=0)


def merge_dimension_value_results(
    es_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    limit: int,
    rrf_k: int,
) -> list[dict[str, Any]]:
    """通过精确匹配置顶和 RRF 融合两套不可直接比较的检索分数。"""
    candidates: dict[tuple[str, str], _MergedCandidate] = {}

    for rank, hit in enumerate(es_hits, start=1):
        source = hit["source"]
        key = _candidate_key(source)
        candidate = candidates.setdefault(key, _MergedCandidate(source=source))
        matched_queries = list(hit.get("matched_queries", []))
        candidate.exact_priority = max(
            candidate.exact_priority,
            _best_exact_priority(matched_queries),
        )
        candidate.rrf_score += 1 / (rrf_k + rank)
        candidate.es_score = float(hit["score"])
        candidate.match_types.update(matched_queries or ["full_text"])

    for rank, hit in enumerate(vector_hits, start=1):
        source = hit["payload"]
        key = _candidate_key(source)
        candidate = candidates.setdefault(key, _MergedCandidate(source=source))
        candidate.rrf_score += 1 / (rrf_k + rank)
        score = float(hit["score"])
        candidate.vector_score = max(candidate.vector_score or score, score)
        candidate.match_types.add("semantic")

    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            item.exact_priority,
            item.rrf_score,
            item.es_score or 0.0,
            item.vector_score or 0.0,
        ),
        reverse=True,
    )
    return [
        {
            **candidate.source,
            "match_types": sorted(candidate.match_types),
            "exact_match": candidate.exact_priority > 0,
            "rrf_score": round(candidate.rrf_score, 8),
            "es_score": candidate.es_score,
            "vector_score": candidate.vector_score,
        }
        for candidate in ranked[:limit]
    ]


class DimensionValueSearchService:
    """执行维度值混合检索并返回统一结构化候选。"""

    def __init__(
        self,
        embedding_client,
        qdrant_repository: QdrantRepository,
        es_repository: ElasticsearchRepository,
    ) -> None:
        self.embedding_client = embedding_client
        self.qdrant_repository = qdrant_repository
        self.es_repository = es_repository

    def search(self, query_text: str, limit: int | None = None) -> list[dict[str, Any]]:
        """分别召回 ES 和 Qdrant 结果，再执行精确优先的 RRF 融合。"""
        normalized_query = query_text.strip()
        if not normalized_query:
            raise ValueError("维度值检索文本不能为空。")

        config = settings.dimension_value_search
        final_limit = min(limit or config.final_top_k, 50)
        es_hits = self.es_repository.search_dimension_values(
            query_text=normalized_query,
            index_name=settings.elasticsearch.dimension_values_alias,
            limit=config.es_top_k,
        )
        query_vector = self.embedding_client.embed_query(normalized_query)
        vector_hits = self.qdrant_repository.search_points(
            collection_name=settings.qdrant.dimension_values_collection,
            vector=query_vector,
            limit=config.vector_top_k,
            score_threshold=config.vector_score_threshold,
        )
        return merge_dimension_value_results(
            es_hits=es_hits,
            vector_hits=vector_hits,
            limit=final_limit,
            rrf_k=config.rrf_k,
        )
