"""指标元数据业务实体。"""

from dataclasses import dataclass


@dataclass
class MetaMetrics:
    """系统内部统一使用的指标元数据表达。"""

    metric_id: str
    metric_name: str
    business_name: str
    base_table_id: str
    expression_sql: str
    aggregation_type: str
    description: str
    aliases: list[str]
    status: str
    unit: str | None = None
    # expression_sql 之外的粒度和跨表限制，供 SQL 生成上下文使用。
    calculation_grain: str = ""
    aggregation_rule: str = ""
