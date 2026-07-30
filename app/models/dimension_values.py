"""维度值元数据 ORM 模型。"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, JSON, String, Text, TIMESTAMP, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DimensionValueMySQL(Base):
    """维度值元数据表对应的 ORM 模型。"""

    __tablename__ = "dimension_values"
    __table_args__ = {"comment": "AI 维度值检索目录表"}

    value_id: Mapped[str] = mapped_column(String(512), primary_key=True, comment="稳定维度值 ID，格式为 column_id::raw_value")
    dimension_id: Mapped[str] = mapped_column(String(128), nullable=False, comment="所属维度 ID，对应 meta.dimensions.dimension_id")
    column_id: Mapped[str] = mapped_column(String(192), nullable=False, comment="所属字段 ID，对应 meta.columns.column_id")
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False, comment="DW 中真实保存的值，生成 SQL 时必须使用该值")
    normalized_value: Mapped[str] = mapped_column(String(255), nullable=False, comment="规范化后的检索文本")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="标准业务展示名称")
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="别名、同义词和口语表达")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="维度值业务说明")
    value_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="该值在对应 DW 数据中的出现次数")
    semantic_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否写入 Qdrant 语义索引")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", comment="维度值状态，active 表示启用")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="更新时间")
