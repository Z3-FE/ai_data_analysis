"""`meta_columns_semantic` 集合类型。"""

from dataclasses import dataclass

from app.entities.meta.meta_columns import MetaColumns


@dataclass
class QdMetaColumnsPayload(MetaColumns):
    """`meta_columns_semantic` 的扁平 payload 类型。"""

    vector_type: str = ""
    point_key: str = ""
    text: str = ""


@dataclass
class QdMetaColumns:
    """`meta_columns_semantic` 的命中结果类型。"""

    id: str
    score: float
    payload: QdMetaColumnsPayload
    vector: list[float] | None = None
