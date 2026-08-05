"""`meta.metrics` ORM 模型。"""

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaMetricModel(Base):
    __tablename__ = "metrics"
    __table_args__ = {"schema": "meta", "comment": "业务指标元数据"}

    metric_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="指标唯一标识")
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="指标物理名称")
    business_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="指标业务名称")
    base_table_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.tables.table_id"), nullable=False, comment="指标基础表标识"
    )
    expression_sql: Mapped[str] = mapped_column(Text, nullable=False, comment="指标计算表达式")
    aggregation_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="聚合方式")
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="指标单位")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="指标业务说明")
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="指标业务别名列表")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="元数据状态")
