"""ES 维度值索引文档和命中结果实体。"""

from dataclasses import dataclass, field

from app.entities.meta.meta_dimension_values import DimensionValueBase


@dataclass(kw_only=True)
class EsDimensionValueDocument(DimensionValueBase):
    """`meta_dimension_values` ES 文档的 `_source` 字段。"""

    dimension_name: str
    dimension_business_name: str
    column_name: str
    table_id: str
    table_name: str
    database_name: str
    data_type: str


@dataclass(frozen=True)
class EsDimensionValueHit:
    """ES 返回的一条命中结果。"""

    document: EsDimensionValueDocument
    score: float
    matched_queries: list[str] = field(default_factory=list)
