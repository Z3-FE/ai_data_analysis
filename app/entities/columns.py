"""字段元数据业务实体。

字段配置中的业务语义、从数仓补齐的真实类型，以及用于检索的别名状态，
都会汇总到这个对象里，再继续流向元数据库、向量库和 Agent 召回链路。
"""

from dataclasses import dataclass


@dataclass
class Columns:
    """系统内部统一使用的字段元数据表达。"""

    column_id: str
    table_id: str
    column_name: str
    business_name: str
    data_type: str
    semantic_role: str
    is_queryable: bool
    is_aggregatable: bool
    description: str
    aliases: list[str]
    status: str
    table_name: str = ""
