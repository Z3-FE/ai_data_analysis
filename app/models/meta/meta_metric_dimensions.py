"""`meta.metric_dimensions` ORM 模型。"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaMetricDimensionModel(Base):
    __tablename__ = "metric_dimensions"
    __table_args__ = {"schema": "meta", "comment": "指标与维度的兼容关系"}

    metric_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.metrics.metric_id"), primary_key=True, comment="指标标识"
    )
    dimension_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.dimensions.dimension_id"), primary_key=True, comment="维度标识"
    )
    compatibility_note: Mapped[str] = mapped_column(Text, nullable=False, comment="指标与维度的兼容说明")
