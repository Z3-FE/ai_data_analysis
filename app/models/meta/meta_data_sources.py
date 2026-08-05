"""`meta.data_sources` ORM 模型。"""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MetaDataSourceModel(Base):
    __tablename__ = "data_sources"
    __table_args__ = {"schema": "meta", "comment": "数据源配置表"}

    data_source_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="数据源唯一标识")
    data_source_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="数据源名称")
    database_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="数据库名称")
    dialect: Mapped[str] = mapped_column(String(32), nullable=False, comment="数据库方言类型")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="数据源说明")
