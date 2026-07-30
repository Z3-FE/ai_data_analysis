"""`meta_columns_semantic` 集合类型。"""

from dataclasses import dataclass

from app.entities.columns import Columns


@dataclass
class MetaColumnsSemanticPayload(Columns):
    """`meta_columns_semantic` 的扁平 payload 类型。"""

    vector_type: str = ""
    point_key: str = ""
    text: str = ""


@dataclass
class MetaColumnsSemantic:
    """`meta_columns_semantic` 的命中结果类型。"""

    id: str
    score: float
    payload: MetaColumnsSemanticPayload
    vector: list[float] | None = None
