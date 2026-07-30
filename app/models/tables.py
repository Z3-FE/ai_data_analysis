"""表元数据 ORM 模型。"""

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TableMySQL(Base):
    """表元数据表对应的 ORM 模型。"""

    __tablename__ = "tables"
    __table_args__ = {"comment": "DW 表元数据表"}

    table_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="表唯一标识，通常由库名和表名组成")
    data_source_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属数据源 ID，对应 meta.data_sources.data_source_id")
    database_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="数据库名称")
    table_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="物理表名")
    table_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="表类型，如 dimension、fact")
    business_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="表业务名称")
    grain: Mapped[str] = mapped_column(Text, nullable=False, comment="表粒度说明")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="表业务说明")
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="表别名列表，用于语义召回")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="表状态，active 表示启用")
