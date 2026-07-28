"""Repository 层聚合包。

这里按数据域收口真实数据操作：
- meta: MySQL 元数据读取
- dw: DW 数据访问入口
- es: Elasticsearch 索引与检索
- qdrant: 向量集合与检索
"""

from app.repositories.dw_repository import DwRepository
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.meta_repository import (
    list_active_columns_for_embedding,
    list_active_dimension_values,
    list_active_metrics_for_embedding,
    list_active_tables_for_embedding,
    list_tables,
)
from app.repositories.qdrant_repository import QdrantRepository

__all__ = [
    "DwRepository",
    "ElasticsearchRepository",
    "QdrantRepository",
    "list_tables",
    "list_active_tables_for_embedding",
    "list_active_columns_for_embedding",
    "list_active_metrics_for_embedding",
    "list_active_dimension_values",
]
