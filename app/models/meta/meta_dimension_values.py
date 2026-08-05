"""`meta.dimension_values` ORM 模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, JSON, String, Text, TIMESTAMP, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaDimensionValueModel(Base):
    __tablename__ = "dimension_values"
    __table_args__ = {"schema": "meta", "comment": "维度真实值元数据"}

    value_id: Mapped[str] = mapped_column(String(512), primary_key=True, comment="维度值唯一标识")
    dimension_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("meta.dimensions.dimension_id"), nullable=False, comment="所属维度标识"
    )
    column_id: Mapped[str] = mapped_column(
        String(192), ForeignKey("meta.columns.column_id"), nullable=False, comment="所属字段标识"
    )
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False, comment="数据库中的真实值")
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False, comment="用于标准化匹配的值")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="面向用户展示的名称")
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="维度值别名列表")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="维度值业务说明")
    value_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="该值在业务数据中的数量")
    semantic_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否参与语义检索")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="元数据状态")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间"
    )
