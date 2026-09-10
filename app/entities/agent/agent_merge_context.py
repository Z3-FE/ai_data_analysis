"""多路召回合并阶段使用的业务实体。

这些对象只在合并、补齐和排序计算期间使用。合并节点完成后再通过 asdict
转换为 AgentState 和 SSE 可以序列化的普通字典。
"""

from dataclasses import dataclass, field
from enum import StrEnum

from app.entities.meta.meta_columns import MetaColumns
from app.entities.meta.meta_metrics import MetaMetrics
from app.entities.meta.meta_tables import MetaTables


class MatchedSource(StrEnum):
    """合并结果的来源标识及其中文含义。"""

    TABLE_RECALL = ("table_recall", "表语义召回直接命中该表")
    COLUMN_RECALL = ("column_recall", "字段语义召回命中，字段所属表因此成为候选表")
    METRIC_RECALL = ("metric_recall", "指标语义召回命中，指标基础表因此成为候选表")
    DIMENSION_VALUE_RECALL = (
        "dimension_value_recall",
        "维度值召回命中，维度值所属字段和表因此成为候选",
    )
    DIMENSION_VALUE_COMPLETION = (
        "dimension_value_completion",
        "根据维度值召回结果从 Meta MySQL 补齐的所属字段",
    )
    METADATA_COMPLETION = (
        "metadata_completion",
        "根据候选表从 Meta MySQL 补齐的可查询字段",
    )

    description: str

    def __new__(cls, value: str, description: str):
        # Enum 会把成员右侧的元组拆开，并把类本身作为 cls 传入 __new__。
        # MatchedSource 是字符串枚举，因此用 str.__new__ 创建带字符串值的成员。
        member = str.__new__(cls, value)
        member._value_ = value
        member.description = description
        return member


@dataclass
class MatchedDimensionValue:
    """绑定到字段上的一条维度真实值及其召回证据。"""

    value_id: str
    dimension_id: str
    raw_value: str
    normalized_value: str
    display_name: str
    aliases: list[str]
    description: str
    exact_match: bool
    exact_priority: int
    match_types: dict[str, str]
    matched_terms: list[str]
    rrf_score: float
    es_score: float | None
    vector_score: float | None


@dataclass
class MergedColumnInfo:
    """字段元数据及其召回来源和命中的真实值。"""

    column: MetaColumns
    matched_sources: set[MatchedSource] = field(default_factory=set)
    matched_values: list[MatchedDimensionValue] = field(default_factory=list)


@dataclass
class MergedTableInfo:
    """表元数据、表下可查询字段和候选来源。"""

    table: MetaTables
    matched_sources: set[MatchedSource] = field(default_factory=set)
    columns: dict[str, MergedColumnInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class RelationshipInfo:
    """候选表之间可用的 JOIN 关系。"""

    relationship_id: str
    from_table_id: str
    from_column_name: str
    to_table_id: str
    to_column_name: str
    relationship_type: str
    description: str


@dataclass(frozen=True)
class MetricDimensionInfo:
    """召回指标与召回维度之间的兼容关系和使用限制。"""

    metric_id: str
    dimension_id: str
    compatibility_note: str
    support_level: str = "supported"
    usage_note: str = ""


@dataclass
class MergedMetricInfo:
    """进入后续过滤节点的完整指标候选。"""

    metric: MetaMetrics
    matched_sources: set[MatchedSource] = field(default_factory=set)
