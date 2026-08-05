"""`meta.business_terms` ORM 模型。"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaBusinessTermModel(Base):
    __tablename__ = "business_terms"
    __table_args__ = {"schema": "meta", "comment": "业务术语定义"}

    term_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="术语唯一标识")
    term_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="术语名称")
    definition: Mapped[str] = mapped_column(Text, nullable=False, comment="术语定义")
    related_metric_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("meta.metrics.metric_id"), nullable=True, comment="关联指标标识"
    )
