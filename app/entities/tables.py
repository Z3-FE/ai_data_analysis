"""表元数据业务实体。"""

from dataclasses import dataclass


@dataclass
class Tables:
    """系统内部统一使用的表元数据表达。"""

    table_id: str
    data_source_id: str
    database_name: str
    table_name: str
    table_type: str
    business_name: str
    grain: str
    description: str
    aliases: list[str]
    status: str
