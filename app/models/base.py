"""SQLAlchemy ORM 基类。"""

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


def utcnow() -> datetime:
    """返回与 PostgreSQL TIMESTAMP 字段一致的无时区 UTC 时间，供列默认值使用。"""
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    """所有 ORM 模型的统一基类。"""
    pass
