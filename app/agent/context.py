"""Agent 运行上下文。

Context 保存一次图执行过程中需要复用的外部依赖对象。它不参与 LangGraph
state 合并，避免把连接类、客户端对象塞进业务状态。
"""

from typing import Any, TypedDict

from app.repositories.es.es_dimension_value_repository import DimensionValueSearch
from app.repositories.dw_repository import DwRepository
from app.repositories.mysql.meta.mysql_meta_catalog_repository import MetaCatalogRepository
from app.repositories.qdrant.qa_meta_columns_repository import MetaColumnsSemanticRepository
from app.repositories.qdrant.qa_meta_dimension_values_repository import MetaDimensionValuesSemanticRepository
from app.repositories.qdrant.qa_meta_metrics_repository import MetaMetricsSemanticRepository
from app.repositories.qdrant.qa_meta_tables_repository import MetaTablesSemanticRepository


class AgentContext(TypedDict):
    """LangGraph Runtime 中传递的外部依赖。"""

    llm_client: Any
    embedding_client: Any
    dimension_value_search: DimensionValueSearch
    meta_tables_semantic_repository: MetaTablesSemanticRepository
    meta_columns_semantic_repository: MetaColumnsSemanticRepository
    meta_metrics_semantic_repository: MetaMetricsSemanticRepository
    meta_dimension_values_semantic_repository: MetaDimensionValuesSemanticRepository
    meta_catalog_repository: MetaCatalogRepository
    dw_repository: DwRepository
