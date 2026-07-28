"""meta 数据库 ORM 模型。

这些模型对应 MySQL `meta` 库中的元数据表，只负责描述表结构和关联关系。
业务查询仍优先放在 repository 层，避免模型层混入业务逻辑。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, JSON, String, Text, TIMESTAMP, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaDataSource(Base):
    """数据源注册表，对应 `meta.data_sources`。"""

    __tablename__ = "data_sources"
    __table_args__ = {"schema": "meta"}

    data_source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data_source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    database_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dialect: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class MetaTable(Base):
    """DW 表元数据，对应 `meta.tables`。"""

    __tablename__ = "tables"
    __table_args__ = {"schema": "meta"}

    table_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data_source_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("meta.data_sources.data_source_id"),
        nullable=False,
    )
    database_name: Mapped[str] = mapped_column(String(64), nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    table_type: Mapped[str] = mapped_column(String(32), nullable=False)
    business_name: Mapped[str] = mapped_column(String(128), nullable=False)
    grain: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class MetaColumn(Base):
    """字段元数据，对应 `meta.columns`。"""

    __tablename__ = "columns"
    __table_args__ = {"schema": "meta"}

    column_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    table_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.tables.table_id"),
        nullable=False,
    )
    column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    business_name: Mapped[str] = mapped_column(String(128), nullable=False)
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_queryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_aggregatable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class MetaRelationship(Base):
    """表连接关系元数据，对应 `meta.relationships`。"""

    __tablename__ = "relationships"
    __table_args__ = {"schema": "meta"}

    relationship_id: Mapped[str] = mapped_column(String(192), primary_key=True)
    from_table_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.tables.table_id"),
        nullable=False,
    )
    from_column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    to_table_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.tables.table_id"),
        nullable=False,
    )
    to_column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class MetaMetric(Base):
    """指标定义元数据，对应 `meta.metrics`。"""

    __tablename__ = "metrics"
    __table_args__ = {"schema": "meta"}

    metric_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    business_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_table_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.tables.table_id"),
        nullable=False,
    )
    expression_sql: Mapped[str] = mapped_column(Text, nullable=False)
    aggregation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class MetaDimension(Base):
    """维度定义元数据，对应 `meta.dimensions`。"""

    __tablename__ = "dimensions"
    __table_args__ = {"schema": "meta"}

    dimension_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    dimension_name: Mapped[str] = mapped_column(String(128), nullable=False)
    business_name: Mapped[str] = mapped_column(String(128), nullable=False)
    table_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.tables.table_id"),
        nullable=False,
    )
    column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class MetaMetricDimension(Base):
    """指标与维度兼容关系，对应 `meta.metric_dimensions`。"""

    __tablename__ = "metric_dimensions"
    __table_args__ = {"schema": "meta"}

    metric_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.metrics.metric_id"),
        primary_key=True,
    )
    dimension_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.dimensions.dimension_id"),
        primary_key=True,
    )
    compatibility_note: Mapped[str] = mapped_column(Text, nullable=False)


class MetaSubjectArea(Base):
    """分析主题域，对应 `meta.subject_areas`。"""

    __tablename__ = "subject_areas"
    __table_args__ = {"schema": "meta"}

    subject_area_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    subject_area_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class MetaSubjectAreaMetric(Base):
    """主题域与指标关系，对应 `meta.subject_area_metrics`。"""

    __tablename__ = "subject_area_metrics"
    __table_args__ = {"schema": "meta"}

    subject_area_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.subject_areas.subject_area_id"),
        primary_key=True,
    )
    metric_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.metrics.metric_id"),
        primary_key=True,
    )


class MetaSubjectAreaDimension(Base):
    """主题域与维度关系，对应 `meta.subject_area_dimensions`。"""

    __tablename__ = "subject_area_dimensions"
    __table_args__ = {"schema": "meta"}

    subject_area_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.subject_areas.subject_area_id"),
        primary_key=True,
    )
    dimension_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.dimensions.dimension_id"),
        primary_key=True,
    )


class MetaBusinessTerm(Base):
    """业务术语，对应 `meta.business_terms`。"""

    __tablename__ = "business_terms"
    __table_args__ = {"schema": "meta"}

    term_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    term_name: Mapped[str] = mapped_column(String(128), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    related_metric_id: Mapped[str | None] = mapped_column(
        String(128),
        ForeignKey("meta.metrics.metric_id"),
        nullable=True,
    )


class MetaQueryExample(Base):
    """示例问题与 SQL，对应 `meta.query_examples`。"""

    __tablename__ = "query_examples"
    __table_args__ = {"schema": "meta"}

    example_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class MetaDimensionValue(Base):
    """字段真实取值目录，对应 `meta.dimension_values`。"""

    __tablename__ = "dimension_values"
    __table_args__ = {"schema": "meta"}

    value_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    dimension_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("meta.dimensions.dimension_id"),
        nullable=False,
    )
    column_id: Mapped[str] = mapped_column(
        String(192),
        ForeignKey("meta.columns.column_id"),
        nullable=False,
    )
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    value_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    semantic_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


MetaJson = dict[str, Any]
