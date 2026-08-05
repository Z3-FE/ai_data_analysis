"""Agent 跨 ES/Qdrant 召回和融合过程实体。"""

from dataclasses import dataclass, field
from typing import Any

MATCH_TYPE_DESCRIPTIONS = {
    "raw_value_exact": "数据库真实值精确匹配",
    "display_name_exact": "中文展示名称精确匹配",
    "alias_exact": "别名精确匹配",
    "full_text": "Elasticsearch 全文匹配",
    "semantic": "Qdrant 向量语义匹配",
}


def match_types_to_state(match_types: set[str]) -> dict[str, str]:
    """把内部命中类型集合转换为便于查看的“类型标识 -> 中文含义”映射。"""
    return {
        match_type: MATCH_TYPE_DESCRIPTIONS[match_type]
        for match_type in sorted(match_types)
    }


@dataclass
class DimensionValueRetrievalCandidate:
    """ES 与 Qdrant 融合过程中的一条维度值候选。"""

    source: dict[str, Any]
    exact_priority: int = 0
    rrf_score: float = 0.0
    es_score: float | None = None
    vector_score: float | None = None
    match_types: set[str] = field(default_factory=set)
    matched_terms: set[str] = field(default_factory=set)
