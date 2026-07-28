"""字段真实取值业务实体。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DimensionValueInfo:
    """字段具体取值及其所属字段的业务表达。"""

    value_id: str
    dimension_id: str
    dimension_name: str
    dimension_business_name: str
    column_id: str
    column_name: str
    table_id: str
    table_name: str
    database_name: str
    data_type: str
    raw_value: str
    normalized_value: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    value_count: int = 0
    semantic_enabled: bool = True
    status: str = "active"
