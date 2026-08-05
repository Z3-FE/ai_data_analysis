"""元数据相关的 HTTP 接口。

这些接口会把语义元数据暴露给后续 AI 分析层使用，让 AI 能理解当前有哪些
DW 表、指标、维度和表关系。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_meta_session
from app.schemas.meta import MetaTable
from app.services.meta_catalog_service import list_meta_tables

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/tables", response_model=list[MetaTable])
async def get_meta_tables(db: AsyncSession = Depends(get_meta_session)) -> list[MetaTable]:
    """返回元数据库中登记的 DW 表清单。"""
    return await db.run_sync(list_meta_tables)
