"""FastAPI 依赖组装。

集中声明 API 层需要的依赖函数，把 Client、Repository 和 Service 的创建细节
收敛在这里，避免路由函数直接创建底层基础设施对象。
"""

from typing import Annotated, Any, TypeVar

from fastapi import Depends

from app.clients.elasticsearch_client import elasticsearch_client_manager
from app.clients.embedding_client import embedding_client_manager
from app.clients.llm_client import llm_client_manager
from app.clients.mysql_client import (
    app_mysql_client_manager,
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client import qdrant_client_manager
from app.repositories.dw_repository import DwRepository
from app.repositories.conversation_repository import ConversationRepository
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


def get_conversation_repository() -> ConversationRepository:
    """获取独立应用库会话历史仓储。"""
    session_factory = _require_initialized(
        app_mysql_client_manager.session_factory,
        "Agent App MySQL Session 工厂",
    )
    return ConversationRepository(session_factory=session_factory)


async def get_meta_catalog_repository(
    session=Depends(get_meta_session),
) -> MetaCatalogRepository:
    """创建 Agent 运行期使用的 Meta MySQL 元数据仓储。"""
    return MetaCatalogRepository(session=session)


async def get_dw_repository() -> DwRepository:
    """创建 Agent 执行 SQL 使用的 DW 仓库。"""
    session_factory = _require_initialized(
        dw_mysql_client_manager.session_factory,
        "DW MySQL Session 工厂",
    )
    async with session_factory() as session:
        yield DwRepository(session=session)


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


def get_dimension_value_search() -> DimensionValueSearch:
    """创建 Agent 在线维度值 ES 检索对象。"""
    client = _require_initialized(
        elasticsearch_client_manager.client,
        "Elasticsearch 客户端",
    )
    return DimensionValueSearch(client=client)


def get_agent_service(
    llm_client: Annotated[Any, Depends(get_llm_client)],
    embedding_client: Annotated[Any, Depends(get_embedding_client)],
    dimension_value_search: Annotated[DimensionValueSearch, Depends(get_dimension_value_search)],
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
    meta_catalog_repository: Annotated[
        MetaCatalogRepository, Depends(get_meta_catalog_repository)
    ],
    dw_repository: Annotated[DwRepository, Depends(get_dw_repository)],
    conversation_repository: Annotated[
        ConversationRepository, Depends(get_conversation_repository)
    ],
) -> AgentService:
    """组装一次 Agent 执行所需的服务。"""
    return AgentService(
        llm_client=llm_client,
        embedding_client=embedding_client,
        dimension_value_search=dimension_value_search,
        meta_tables_semantic_repository=meta_tables_semantic_repository,
        meta_columns_semantic_repository=meta_columns_semantic_repository,
        meta_metrics_semantic_repository=meta_metrics_semantic_repository,
        meta_dimension_values_semantic_repository=meta_dimension_values_semantic_repository,
        meta_catalog_repository=meta_catalog_repository,
        dw_repository=dw_repository,
        conversation_repository=conversation_repository,
    )
