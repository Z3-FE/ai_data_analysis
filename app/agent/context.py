"""Agent 运行上下文。

Context 保存一次图执行过程中需要复用的外部依赖对象。它不参与 LangGraph
state 合并，避免把连接类、客户端对象塞进业务状态。
"""

from typing import TypedDict

from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.qdrant_repository import QdrantRepository


class AgentContext(TypedDict):
    """LangGraph Runtime 中传递的外部依赖。"""

    llm_client: LLMClient
    embedding_client: EmbeddingClient
    qdrant_repository: QdrantRepository
    elasticsearch_repository: ElasticsearchRepository
