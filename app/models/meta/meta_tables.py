"""`meta.tables` ORM 模型。"""

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaTableModel(Base):
    __tablename__ = "tables"
    __table_args__ = {"schema": "meta", "comment": "数据仓库表元数据"}

    table_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="表唯一标识，通常为数据库名加表名")
    data_source_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("meta.data_sources.data_source_id"), nullable=False, comment="所属数据源标识"
    )
    database_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="物理数据库名称")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="物理表名")
    table_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="表类型，如事实表或维度表")
    business_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="表的业务名称")
    grain: Mapped[str] = mapped_column(Text, nullable=False, comment="表的数据粒度")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="表的业务说明")
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="表的业务别名列表")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="元数据状态")
