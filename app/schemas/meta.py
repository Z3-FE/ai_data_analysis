"""元数据接口的 Pydantic 响应结构。

Schema 用来定义 API 返回的 JSON 形状，避免路由函数把数据库里的非预期字段
直接暴露给前端或调用方。
"""

from pydantic import BaseModel, ConfigDict


class MetaTable(BaseModel):
    """`meta.tables` 中一行表元数据对应的接口返回模型。"""
    model_config = ConfigDict(from_attributes=True)

    table_id: str
    database_name: str
    table_name: str
    table_type: str
    business_name: str
    grain: str | None = None
    description: str | None = None


class DimensionValueSearchResult(BaseModel):
    """ES + Qdrant 混合检索返回的一条维度值候选。"""

    value_id: str
    dimension_id: str
    dimension_name: str
    dimension_business_name: str
    column_id: str
    column_name: str
    table_id: str
    table_name: str
    database_name: str
    data_type: str
    raw_value: str
    normalized_value: str
    display_name: str
    aliases: list[str]
    description: str
    value_count: int
    semantic_enabled: bool
    status: str
    match_types: list[str]
    exact_match: bool
    rrf_score: float
    es_score: float | None = None
    vector_score: float | None = None
