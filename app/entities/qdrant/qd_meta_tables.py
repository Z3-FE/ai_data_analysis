"""`meta_tables_semantic` 集合类型。"""

from dataclasses import dataclass

from app.entities.meta.meta_tables import MetaTables


@dataclass
class QdMetaTablesPayload(MetaTables):
    """`meta_tables_semantic` 的扁平 payload 类型。"""

    vector_type: str = ""
    point_key: str = ""
    text: str = ""


@dataclass
class QdMetaTables:
    """`meta_tables_semantic` 的命中结果类型。"""

    id: str
    score: float
    payload: QdMetaTablesPayload
    vector: list[float] | None = None
