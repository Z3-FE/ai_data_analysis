"""FastAPI 依赖组装。

集中声明 API 层需要的依赖函数，把 Client、Repository 和 Service 的创建细节
收敛在这里，避免路由函数直接创建底层基础设施对象。
"""

from typing import Annotated

from fastapi import Depends

from app.clients.embedding_client import EmbeddingClient
from app.clients.elasticsearch_client import FullTextSearchClient
from app.clients.llm_client import LLMClient
from app.clients.qdrant_client import VectorDbClient
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.qdrant_repository import QdrantRepository
from app.services.agent_service import AgentService


def get_llm_client() -> LLMClient:
    """创建 LLM 客户端。"""
    return LLMClient()


def get_embedding_client() -> EmbeddingClient:
    """创建 Embedding 客户端。"""
    return EmbeddingClient()


def get_qdrant_repository() -> QdrantRepository:
    """创建 Qdrant 仓库。"""
    return QdrantRepository(client=VectorDbClient())


def get_elasticsearch_repository() -> ElasticsearchRepository:
    """创建 Elasticsearch 仓库。"""
    return ElasticsearchRepository(client=FullTextSearchClient())


def get_agent_service(
    llm_client: Annotated[LLMClient, Depends(get_llm_client)],
    embedding_client: Annotated[EmbeddingClient, Depends(get_embedding_client)],
    qdrant_repository: Annotated[QdrantRepository, Depends(get_qdrant_repository)],
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
    )
