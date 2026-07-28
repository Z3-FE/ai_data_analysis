"""MySQL 客户端和 SQLAlchemy Session 管理。

MySQL 是当前项目的结构化数据核心：`dw` 保存数仓事实表和维度表，`meta`
保存语义元数据。这里统一创建 MySQL engine 和 Session 工厂。
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine_options = {
    "pool_pre_ping": settings.mysql.pool_pre_ping,
    "pool_recycle": settings.mysql.pool_recycle,
}

meta_engine = create_engine(settings.mysql.meta_database_url, **engine_options)
dw_engine = create_engine(settings.mysql.dw_database_url, **engine_options)

MetaSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=meta_engine,
)
DwSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=dw_engine,
)


def get_meta_db() -> Generator[Session, None, None]:
    """为一次请求提供连接 `meta` 数据库的 SQLAlchemy Session。"""
    db = MetaSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_dw_db() -> Generator[Session, None, None]:
    """为一次请求提供连接 `dw` 数据库的 SQLAlchemy Session。"""
    db = DwSessionLocal()
    try:
        yield db
    finally:
        db.close()
