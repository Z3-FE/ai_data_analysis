"""Qdrant 通用类型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QdrantSearchHit:
    """Qdrant 检索命中的通用表达。"""

    id: str
    score: float
    payload: dict[str, Any]
    vector: list[float] | None = None


@dataclass
class QdrantPoint:
    """Qdrant point 的通用表达。"""

    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)

