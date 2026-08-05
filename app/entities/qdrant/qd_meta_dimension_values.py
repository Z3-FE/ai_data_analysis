"""`meta_dimension_values_semantic` 集合类型。"""

from dataclasses import dataclass
from typing import Any

from app.entities.es.es_dimension_value import EsDimensionValueDocument


@dataclass(kw_only=True)
class QdMetaDimensionValuePayload(EsDimensionValueDocument):
    """`meta_dimension_values_semantic` 集合实际保存的扁平 payload。"""

    vector_type: str = ""
    point_key: str = ""
    text: str = ""


@dataclass(frozen=True)
class QdMetaDimensionValue:
    """`meta_dimension_values_semantic` 的命中结果类型。"""

    id: str
    score: float
    payload: QdMetaDimensionValuePayload
    vector: list[float] | None = None


@dataclass(frozen=True)
class QdMetaDimensionValueDocument:
    """一条待写入 `meta_dimension_values_semantic` 的向量文档。"""

    point_id: str
    point_key: str
    vector_type: str
    text: str
    payload: dict[str, Any]
