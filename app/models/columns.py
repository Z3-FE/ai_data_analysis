"""字段元数据 ORM 模型。"""

from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ColumnMySQL(Base):
    """字段元数据表对应的 ORM 模型。"""

    __tablename__ = "columns"
    __table_args__ = {"comment": "字段元数据表"}

    column_id: Mapped[str] = mapped_column(String(192), primary_key=True, comment="字段唯一标识，通常由库名、表名和字段名组成")
    table_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="所属表 ID，对应 meta.tables.table_id")
    column_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="字段物理名称")
    business_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="字段业务名称")
    data_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="字段数据类型")
    semantic_role: Mapped[str] = mapped_column(String(32), nullable=False, comment="字段语义角色，如 dimension、measure、time、identifier")
    is_queryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否允许在问数查询中使用")
    is_aggregatable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否允许作为度量字段聚合")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="字段业务说明")
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="字段别名列表，用于语义召回")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="字段状态，active 表示启用")
