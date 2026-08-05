"""`meta.relationships` ORM 模型。"""

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaRelationshipModel(Base):
    __tablename__ = "relationships"
    __table_args__ = {"schema": "meta", "comment": "表之间的关联关系"}

    relationship_id: Mapped[str] = mapped_column(String(192), primary_key=True, comment="关联关系唯一标识")
    from_table_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.tables.table_id"), nullable=False, comment="起始表标识"
    )
    from_column_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="起始表字段名")
    to_table_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.tables.table_id"), nullable=False, comment="目标表标识"
    )
    to_column_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="目标表字段名")
    relationship_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="关联关系类型")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="关联关系说明")
