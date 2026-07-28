"""元数据相关的 HTTP 接口。

这些接口会把语义元数据暴露给后续 AI 分析层使用，让 AI 能理解当前有哪些
DW 表、指标、维度和表关系。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.clients.mysql_client import get_meta_db
from app.schemas.meta import DimensionValueSearchResult, MetaTable
from app.services.meta_catalog_service import list_meta_tables
from app.services.semantic.dimension_value_search_service import (
    DimensionValueSearchService,
)

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/tables", response_model=list[MetaTable])
def get_meta_tables(db: Session = Depends(get_meta_db)) -> list[MetaTable]:
    """返回元数据库中登记的 DW 表清单。"""
    return list_meta_tables(db)


@router.get(
    "/dimension-values/search",
    response_model=list[DimensionValueSearchResult],
)
def search_dimension_values(
    query: str,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[DimensionValueSearchResult]:
    """使用 Elasticsearch 和 Qdrant 混合检索真实维度值。"""
    results = DimensionValueSearchService().search(query_text=query, limit=limit)
    return [DimensionValueSearchResult.model_validate(result) for result in results]
