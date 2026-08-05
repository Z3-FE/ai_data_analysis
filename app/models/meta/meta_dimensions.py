"""`meta.dimensions` ORM 模型。"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaDimensionModel(Base):
    __tablename__ = "dimensions"
    __table_args__ = {"schema": "meta", "comment": "业务维度元数据"}

    dimension_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="维度唯一标识")
    dimension_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="维度物理名称")
    business_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="维度业务名称")
    table_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.tables.table_id"), nullable=False, comment="维度所属表标识"
    )
    column_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="维度对应字段名")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="维度业务说明")
