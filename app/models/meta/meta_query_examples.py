"""`meta.query_examples` ORM 模型。"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaQueryExampleModel(Base):
    __tablename__ = "query_examples"
    __table_args__ = {"schema": "meta", "comment": "问数查询示例"}

    example_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="示例唯一标识")
    question: Mapped[str] = mapped_column(Text, nullable=False, comment="用户问题")
    sql_text: Mapped[str] = mapped_column(Text, nullable=False, comment="示例 SQL")
    explanation: Mapped[str] = mapped_column(Text, nullable=False, comment="SQL 说明")
