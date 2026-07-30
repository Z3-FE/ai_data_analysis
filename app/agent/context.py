"""Agent 运行上下文。

Context 保存一次图执行过程中需要复用的外部依赖对象。它不参与 LangGraph
state 合并，避免把连接类、客户端对象塞进业务状态。
"""

from typing import Any, TypedDict

from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.qdrant.meta_columns_semantic_repository import MetaColumnsSemanticRepository
from app.repositories.qdrant.meta_dimension_values_semantic_repository import MetaDimensionValuesSemanticRepository
from app.repositories.qdrant.meta_metrics_semantic_repository import MetaMetricsSemanticRepository
from app.repositories.qdrant.meta_tables_semantic_repository import MetaTablesSemanticRepository
from app.repositories.qdrant_repository import QdrantRepository


class AgentContext(TypedDict):
    """LangGraph Runtime 中传递的外部依赖。"""

    llm_client: Any
    embedding_client: Any
    qdrant_repository: QdrantRepository
    elasticsearch_repository: ElasticsearchRepository
    meta_tables_semantic_repository: MetaTablesSemanticRepository
    meta_columns_semantic_repository: MetaColumnsSemanticRepository
    meta_metrics_semantic_repository: MetaMetricsSemanticRepository
    meta_dimension_values_semantic_repository: MetaDimensionValuesSemanticRepository
