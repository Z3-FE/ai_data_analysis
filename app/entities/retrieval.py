"""检索召回与融合过程中的业务实体。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RetrievalHit:
    """单一路径召回结果，例如 ES 命中或 Qdrant 命中。"""

    source_name: str
    entity_id: str
    score: float
    payload: dict[str, Any]
    match_types: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MergedRetrievalResult:
    """多路召回融合后的统一候选结果。"""

    entity_id: str
    payload: dict[str, Any]
    match_types: list[str]
    exact_match: bool
    rrf_score: float
    es_score: float | None = None
    vector_score: float | None = None
