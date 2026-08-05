"""`meta.dimension_values` 业务实体。"""

from dataclasses import dataclass, field
from datetime import datetime

"""
frozen=True：对象创建后，字段不能重新赋值。
kw_only=True：创建对象时，必须写出参数名称。
"""

@dataclass(kw_only=True)
class DimensionValueBase:
    """维度值在 Meta、ES 和 Qdrant 中共同使用的基础字段。"""

    value_id: str
    dimension_id: str
    column_id: str
    raw_value: str
    normalized_value: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    value_count: int = 0
    semantic_enabled: bool = True
    status: str = "active"


@dataclass(kw_only=True)
class MetaDimensionValue(DimensionValueBase):
    """与 Meta MySQL 维度值模型对齐的业务实体。"""

    created_at: datetime | None = None
    updated_at: datetime | None = None
