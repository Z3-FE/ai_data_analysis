"""FastAPI 依赖组装。

集中声明 API 层需要的依赖函数，把 Client、Repository 和 Service 的创建细节
收敛在这里，避免路由函数直接创建底层基础设施对象。
"""

from typing import Annotated, Any, TypeVar

from fastapi import Depends

from app.clients.embedding_client import embedding_client_manager
from app.clients.elasticsearch_client import elasticsearch_client_manager
from app.clients.llm_client import llm_client_manager
from app.clients.mysql_client import meta_mysql_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.qdrant.meta_columns_semantic_repository import MetaColumnsSemanticRepository
from app.repositories.qdrant.meta_dimension_values_semantic_repository import MetaDimensionValuesSemanticRepository
from app.repositories.qdrant.meta_metrics_semantic_repository import MetaMetricsSemanticRepository
from app.repositories.qdrant.meta_tables_semantic_repository import MetaTablesSemanticRepository
from app.repositories.qdrant_repository import QdrantRepository
from app.services.agent_service import AgentService

T = TypeVar("T")


def _require_initialized(resource: T | None, resource_name: str) -> T:
    """确保应用级资源已经在 lifespan 启动阶段完成初始化。"""
    if resource is None:
        raise RuntimeError(f"{resource_name}尚未初始化")
    return resource


def get_llm_client() -> Any:
    """获取初始化好的 LLM 插件对象。"""
    return _require_initialized(llm_client_manager.client, "LLM 客户端")


def get_embedding_client() -> Any:
    """获取初始化好的 Embedding 客户端。"""
    return _require_initialized(embedding_client_manager.client, "Embedding 客户端")


async def get_meta_session():
    """创建一次请求内使用的 meta 数据库 Session。"""
    session_factory = _require_initialized(
        meta_mysql_client_manager.session_factory,
        "Meta MySQL Session 工厂",
    )
    async with session_factory() as session:
        yield session


def get_qdrant_repository() -> QdrantRepository:
    """创建 Qdrant 通用仓库。"""
    client = _require_initialized(qdrant_client_manager.client, "Qdrant 客户端")
    return QdrantRepository(client=client)


async def get_meta_tables_semantic_repository() -> MetaTablesSemanticRepository:
    """创建 meta_tables_semantic 仓库。"""
    client = _require_initialized(qdrant_client_manager.client, "Qdrant 客户端")
    return MetaTablesSemanticRepository(client=client)


async def get_meta_columns_semantic_repository() -> MetaColumnsSemanticRepository:
    """创建 meta_columns_semantic 仓库。"""
    client = _require_initialized(qdrant_client_manager.client, "Qdrant 客户端")
    return MetaColumnsSemanticRepository(client=client)


async def get_meta_metrics_semantic_repository() -> MetaMetricsSemanticRepository:
    """创建 meta_metrics_semantic 仓库。"""
    client = _require_initialized(qdrant_client_manager.client, "Qdrant 客户端")
    return MetaMetricsSemanticRepository(client=client)


async def get_meta_dimension_values_semantic_repository() -> MetaDimensionValuesSemanticRepository:
    """创建 meta_dimension_values_semantic 仓库。"""
    client = _require_initialized(qdrant_client_manager.client, "Qdrant 客户端")
    return MetaDimensionValuesSemanticRepository(client=client)


def get_elasticsearch_repository() -> ElasticsearchRepository:
    """创建 Elasticsearch 仓库。"""
    client = _require_initialized(
        elasticsearch_client_manager.client,
        "Elasticsearch 客户端",
    )
    return ElasticsearchRepository(client=client)


def get_agent_service(
    llm_client: Annotated[Any, Depends(get_llm_client)],
    embedding_client: Annotated[Any, Depends(get_embedding_client)],
    qdrant_repository: Annotated[QdrantRepository, Depends(get_qdrant_repository)],
    meta_tables_semantic_repository: Annotated[
        MetaTablesSemanticRepository,
        Depends(get_meta_tables_semantic_repository),
    ],
    meta_columns_semantic_repository: Annotated[
        MetaColumnsSemanticRepository,
        Depends(get_meta_columns_semantic_repository),
    ],
    meta_metrics_semantic_repository: Annotated[
        MetaMetricsSemanticRepository,
        Depends(get_meta_metrics_semantic_repository),
    ],
    meta_dimension_values_semantic_repository: Annotated[
        MetaDimensionValuesSemanticRepository,
        Depends(get_meta_dimension_values_semantic_repository),
    ],
    elasticsearch_repository: Annotated[
        ElasticsearchRepository, Depends(get_elasticsearch_repository)
    ],
) -> AgentService:
    """组装一次 Agent 执行所需的服务。"""
    return AgentService(
        llm_client=llm_client,
        embedding_client=embedding_client,
        qdrant_repository=qdrant_repository,
        elasticsearch_repository=elasticsearch_repository,
        meta_tables_semantic_repository=meta_tables_semantic_repository,
        meta_columns_semantic_repository=meta_columns_semantic_repository,
        meta_metrics_semantic_repository=meta_metrics_semantic_repository,
        meta_dimension_values_semantic_repository=meta_dimension_values_semantic_repository,
    )
