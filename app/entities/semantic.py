"""语义索引构建过程中的业务实体。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SemanticText:
    """一段需要生成向量的文本及其来源信息。"""

    entity_type: str
    entity_id: str
    vector_type: str
    text: str
    point_key: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorPointInfo:
    """准备写入 Qdrant 的向量点业务表达。"""

    point_id: str
    point_key: str
    collection_name: str
    vector_type: str
    text: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)
