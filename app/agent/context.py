"""Agent 运行上下文。

Context 保存一次图执行过程中需要复用的外部依赖对象。它不参与 LangGraph
state 合并，避免把连接类、客户端对象塞进业务状态。
"""

import asyncio
from typing import Any, TypedDict

from app.repositories.dw_repository import DwRepository
from app.repositories.es.es_dimension_value_repository import DimensionValueSearch
from app.repositories.mysql.meta.mysql_meta_catalog_repository import (
    MetaCatalogRepository,
)
from app.repositories.qdrant.qa_meta_columns_repository import (
    MetaColumnsSemanticRepository,
)
from app.repositories.qdrant.qa_meta_dimension_values_repository import (
    MetaDimensionValuesSemanticRepository,
)
from app.repositories.qdrant.qa_meta_metrics_repository import (
    MetaMetricsSemanticRepository,
)
from app.repositories.qdrant.qa_meta_tables_repository import (
    MetaTablesSemanticRepository,
)
from app.core.config import settings


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
    # LLM 流式响应的空闲超时时间，由 config.yaml 的 llm.timeout_seconds 注入。
    # 每收到一条流式事件都会重新计时，不限制整个节点的累计执行时长。
    llm_timeout_seconds: float
    # 同一次 query_data 内四路元数据召回共享的 Embedding/Qdrant/ES 并发闸门。
    metadata_recall_semaphore: asyncio.Semaphore


def get_metadata_recall_semaphore(context: AgentContext) -> asyncio.Semaphore:
    """返回本次问数共享的元数据召回闸门；测试或独立节点调用时按配置懒创建。"""
    semaphore = context.get("metadata_recall_semaphore")
    if semaphore is None:
        semaphore = asyncio.Semaphore(settings.metadata_recall.max_concurrent_terms)
        context["metadata_recall_semaphore"] = semaphore
    return semaphore
