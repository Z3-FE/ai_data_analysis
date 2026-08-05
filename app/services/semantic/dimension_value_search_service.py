"""维度值的 ES + Qdrant 两级混合检索。

第一层：一个 recall_term 内部，把 ES 和 Qdrant 的结果合并。
第二层：所有 recall_term 完成后，把相同业务值去重并汇总。
"""

from typing import Any

from app.entities.agent.agent_retrieval import (
    DimensionValueRetrievalCandidate,
    match_types_to_state,
)

_EXACT_PRIORITY = {
    "raw_value_exact": 3,
    "display_name_exact": 2,
    "alias_exact": 1,
}
_QDRANT_POINT_FIELDS = {"vector_type", "point_key", "text"}


def _candidate_key(source: dict[str, Any]) -> tuple[str, str]:
    """用字段 ID 和真实值定位同一条业务候选。"""
    return str(source["column_id"]), str(source["raw_value"])


def _best_exact_priority(matched_queries: list[str]) -> int:
    """取当前 ES 命中的最高精确匹配等级。"""
    return max((_EXACT_PRIORITY.get(name, 0) for name in matched_queries), default=0)


def _to_result(candidate: DimensionValueRetrievalCandidate) -> dict[str, Any]:
    """把内部候选转换为后续 Agent 使用的普通字典。"""
    return {
        **candidate.source,
        "match_types": match_types_to_state(candidate.match_types),
        "matched_terms": sorted(candidate.matched_terms),
        "exact_priority": candidate.exact_priority,
        "exact_match": candidate.exact_priority > 0,
        "rrf_score": round(candidate.rrf_score, 8),
        "es_score": candidate.es_score,
        "vector_score": candidate.vector_score,
    }


def _qdrant_business_source(payload: dict[str, Any]) -> dict[str, Any]:
    """从 Qdrant payload 中取出维度值业务字段。"""
    return {key: value for key, value in payload.items() if key not in _QDRANT_POINT_FIELDS}


def merge_term_results(
    recall_term: str,
    es_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    limit: int,
    rrf_k: int,
) -> list[DimensionValueRetrievalCandidate]:
    """合并一个关键词对应的 ES 和 Qdrant 结果。

    两个列表的排名都从 1 开始，因此每个关键词拥有独立的 ES/Qdrant 融合排名。
    limit 是当前关键词的最大保留数量，不代表一定会返回 limit 条。
    """
   # print(f"####################recall_term:{recall_term}/n")
    candidates: dict[tuple[str, str], DimensionValueRetrievalCandidate] = {}
    vector_ranked_keys: set[tuple[str, str]] = set()

    # 步骤 1：处理当前关键词的 ES 结果。
    # 这里 rank 是当前关键词内部的排名，不受其他关键词结果影响。
    # 当前 ES 一条维度值对应一条文档，因此一次关键词召回的列表中不会有重复 key。
    for rank, hit in enumerate(es_hits, start=1):
        source = hit["source"]
        key = _candidate_key(source)
        # ES 的业务 key 不会重复，直接创建候选并写入融合字典。
        candidate = DimensionValueRetrievalCandidate(source=source)

        candidates[key] = candidate
        matched_queries = list(hit.get("matched_queries", []))
        candidate.exact_priority = _best_exact_priority(matched_queries)
        # 记录 ES 这一路对当前候选的 RRF 排名贡献。
        candidate.rrf_score = 1 / (rrf_k + rank)
        candidate.es_score = float(hit["score"])
        candidate.match_types.update(matched_queries or {"full_text"})
        candidate.matched_terms.add(recall_term)

    # print(f"####################candidates:{candidates}/n")

    # 步骤 2：处理当前关键词的 Qdrant 结果 (已经排好顺序了)。
    for rank, hit in enumerate(vector_hits, start=1):
        source = hit["payload"]
        key = _candidate_key(source)
        #setdefault 如果有key，就不复制，还是使用原有的key对应的value，如果没有就直接写入(key,value)
        candidate = candidates.get(key)
        if candidate is None:
            # ES 没有召回该业务值时，由 Qdrant 创建候选。
            # source 只保存维度值业务字段，不保留某一个向量点的说明字段。
            candidate = DimensionValueRetrievalCandidate(
                source=_qdrant_business_source(source)
            )
            candidates[key] = candidate
        # 一个业务值会有标准名、别名、说明等多个向量点。
        # 只使用其中排名最高的点贡献 RRF，避免向量点越多得分越高。
        if key not in vector_ranked_keys:
            candidate.rrf_score += 1 / (rrf_k + rank)
            vector_ranked_keys.add(key)
        score = float(hit["score"])
        candidate.vector_score = max(candidate.vector_score or score, score)
        candidate.match_types.add("semantic")
        candidate.matched_terms.add(recall_term)
    # print(f"####################步骤2：candidates:{candidates}/n")
    # 步骤 3：当前关键词内部按“精确命中优先，再看融合分数”排序。
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
    # 第一层仍处于融合计算过程，直接返回候选对象，不提前转换成字典。
    return ranked[:limit]


def merge_all_term_results(
    term_results: list[list[DimensionValueRetrievalCandidate]],
    total_limit: int,
    rrf_k: int,
) -> list[dict[str, Any]]:
    """合并所有关键词的结果，并按总上限返回。

    相同的 `(column_id, raw_value)` 只保留一条；不同关键词对它的支持会汇总到
    `matched_terms` 和 `rrf_score` 中。total_limit 是防止上下文过大的安全上限。
    """
    candidates: dict[tuple[str, str], DimensionValueRetrievalCandidate] = {}

    # 每个关键词已经完成内部融合；这里的 rank 只表示该关键词融合后的名次。
    for term_candidates in term_results:
        for rank, term_candidate in enumerate(term_candidates, start=1):
            candidate = candidates.setdefault(
                _candidate_key(term_candidate.source),
                DimensionValueRetrievalCandidate(source=term_candidate.source),
            )
            candidate.exact_priority = max(
                candidate.exact_priority,
                term_candidate.exact_priority,
            )
            # 同一候选被多个关键词召回时，累计每个关键词的融合证据。
            candidate.rrf_score += term_candidate.rrf_score + 1 / (rrf_k + rank)
            candidate.es_score = _max_score(candidate.es_score, term_candidate.es_score)
            candidate.vector_score = _max_score(
                candidate.vector_score, term_candidate.vector_score
            )
            candidate.match_types.update(term_candidate.match_types)
            candidate.matched_terms.update(term_candidate.matched_terms)

    # 多关键词共同支持的候选会自然获得更多 matched_terms，但不压过精确匹配。
    ranked = sorted(
        candidates.values(),
        key=lambda item: (
            item.exact_priority,
            item.rrf_score,
            len(item.matched_terms),
            item.es_score or 0.0,
            item.vector_score or 0.0,
        ),
        reverse=True,
    )
    return [_to_result(candidate) for candidate in ranked[:total_limit]]


def _max_score(current: float | None, incoming: float | None) -> float | None:
    """合并两路分数时保留最大值，避免后一个结果覆盖更高分数。"""
    if incoming is None:
        return current
    return incoming if current is None else max(current, incoming)
