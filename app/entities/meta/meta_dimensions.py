"""业务维度元数据实体。"""

from dataclasses import dataclass


@dataclass
class MetaDimensions:
    """与 `meta.dimensions` ORM 模型对齐的业务实体。"""

    dimension_id: str
    dimension_name: str
    business_name: str
    table_id: str
    column_name: str
    description: str
