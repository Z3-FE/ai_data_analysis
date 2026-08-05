"""Meta 主题域相关 ORM 模型。"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaSubjectAreaModel(Base):
    __tablename__ = "subject_areas"
    __table_args__ = {"schema": "meta", "comment": "业务主题域"}

    subject_area_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="主题域唯一标识")
    subject_area_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="主题域名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="主题域说明")


class MetaSubjectAreaMetricModel(Base):
    __tablename__ = "subject_area_metrics"
    __table_args__ = {"schema": "meta", "comment": "主题域与指标关联关系"}

    subject_area_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.subject_areas.subject_area_id"), primary_key=True, comment="主题域标识"
    )
    metric_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.metrics.metric_id"), primary_key=True, comment="指标标识"
    )


class MetaSubjectAreaDimensionModel(Base):
    __tablename__ = "subject_area_dimensions"
    __table_args__ = {"schema": "meta", "comment": "主题域与维度关联关系"}

    subject_area_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.subject_areas.subject_area_id"), primary_key=True, comment="主题域标识"
    )
    dimension_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.dimensions.dimension_id"), primary_key=True, comment="维度标识"
    )
