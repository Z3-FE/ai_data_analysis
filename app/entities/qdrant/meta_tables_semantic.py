"""`meta_tables_semantic` 集合类型。"""

from dataclasses import dataclass

from app.entities.tables import Tables


@dataclass
class MetaTablesSemanticPayload(Tables):
    """`meta_tables_semantic` 的扁平 payload 类型。"""

    vector_type: str = ""
    point_key: str = ""
    text: str = ""


@dataclass
class MetaTablesSemantic:
    """`meta_tables_semantic` 的命中结果类型。"""

    id: str
    score: float
    payload: MetaTablesSemanticPayload
    vector: list[float] | None = None
