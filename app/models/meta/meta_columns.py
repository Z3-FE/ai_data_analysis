"""`meta.columns` ORM 模型。"""

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaColumnModel(Base):
    __tablename__ = "columns"
    __table_args__ = {"schema": "meta", "comment": "数据仓库字段元数据"}

    column_id: Mapped[str] = mapped_column(String(192), primary_key=True, comment="字段唯一标识")
    table_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.tables.table_id"), nullable=False, comment="所属表标识"
    )
    column_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="物理字段名")
    business_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="字段的业务名称")
    data_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="字段数据类型")
    semantic_role: Mapped[str] = mapped_column(String(32), nullable=False, comment="字段语义角色")
    is_queryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否允许查询")
    is_aggregatable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否允许聚合")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="字段业务说明")
    aliases: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="字段业务别名列表")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="元数据状态")
