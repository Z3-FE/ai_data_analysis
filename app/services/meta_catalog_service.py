"""元数据目录业务服务。

这里负责组织 meta 库中的表、字段、指标等目录型元数据读取逻辑，避免把业务
处理直接写在 FastAPI 路由函数或者 SQL Repository 函数里。
"""

from sqlalchemy.orm import Session

from app.repositories import meta_build_test_repository
from app.schemas.meta import MetaTable


def list_meta_tables(db: Session) -> list[MetaTable]:
    """返回经过 Pydantic 校验后的 DW 表元数据。"""
    rows = meta_build_test_repository.list_tables(db)
    return [MetaTable.model_validate(row) for row in rows]
