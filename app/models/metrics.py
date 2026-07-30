"""指标元数据 ORM 模型。"""

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetricMySQL(Base):
    """指标元数据表对应的 ORM 模型。"""

    __tablename__ = "metrics"
    __table_args__ = {"comment": "指标定义元数据表"}

    metric_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="指标唯一标识")
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="指标物理名称或英文名称")
    business_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="指标业务名称")
    base_table_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="指标默认事实表 ID，对应 meta.tables.table_id")
    expression_sql: Mapped[str] = mapped_column(Text, nullable=False, comment="指标 SQL 表达式")
    aggregation_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="聚合类型，如 sum、count、avg")
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="指标单位")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="指标业务口径说明")
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="指标别名列表，用于语义召回")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="指标状态，active 表示启用")
