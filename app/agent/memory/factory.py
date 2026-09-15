"""Memory 模块的应用级组装入口。

这里仅完成依赖注入，不把 Memory 自动接入 agent_graph。上下文工程阶段可以
显式持有 MemoryRuntime，并决定每一轮读取或写入哪些记忆类型。
"""

import logging
from dataclasses import dataclass
from typing import Any

from neo4j import AsyncDriver
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory.eligibility import MemoryEligibilityEvaluator
from app.agent.memory.encoders.text import TextMemoryEncoder
from app.agent.memory.enums import MemoryType
from app.agent.memory.formation_service import MemoryFormationService
from app.agent.memory.governance import MemoryGovernance
from app.agent.memory.graph.schema import ensure_graph_schema
from app.agent.memory.lifecycle import MemoryLifecycle
from app.agent.memory.llm_extractor import LlmMemoryExtractor
from app.agent.memory.manager import MemoryManager
from app.agent.memory.types.episodic import EpisodicMemory
from app.agent.memory.types.perceptual import PerceptualMemory
from app.agent.memory.types.semantic import SemanticMemory
from app.agent.memory.types.working import WorkingMemory
from app.agent.memory.writer import MemoryWriter
from app.core.config import Neo4jConfig, QdrantConfig, settings
from app.repositories.memory.neo4j_graph_repository import Neo4jGraphRepository
from app.repositories.memory.postgres_memory_repository import PostgresMemoryRepository
from app.repositories.memory.qdrant_memory_repository import QdrantMemoryRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MemoryRuntime:
    """已经组装好的记忆模块及其维护能力。"""

    # 上层 Agent 和 ContextEngine 使用的统一记忆入口。
    manager: MemoryManager
    # 过期清理入口；投影失败只记录，不在当前阶段启动消费者。
    lifecycle: MemoryLifecycle
    # 是否已经启用 Qdrant 检索投影。
    vector_enabled: bool
    # 是否已经启用 Semantic Memory 的 Neo4j 图投影。
    graph_enabled: bool
    # 本次组装时 Neo4j 约束和索引是否初始化成功。
    graph_schema_ready: bool
    # 本轮结束后负责形成长期记忆；无 LLM 时仍支持显式“记住”。
    formation_service: MemoryFormationService


async def build_memory_runtime(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    qdrant_client: AsyncQdrantClient | None = None,
    embedding_client: Any = None,
    neo4j_driver: AsyncDriver | None = None,
    qdrant_config: QdrantConfig | None = None,
    neo4j_config: Neo4jConfig | None = None,
    initialize_graph_schema: bool = True,
    strict_graph_schema: bool = False,
    llm_client: Any = None,
) -> MemoryRuntime:
    """按可用基础设施组装 Memory，不改变现有 Agent 图。

    PostgreSQL 是必选事实库。Qdrant 与 Embedding 必须同时提供才会启用向量
    检索；Neo4j 是 Semantic Memory 的可选图投影。图初始化失败默认降级，
    strict_graph_schema=True 时才向调用方抛出异常。
    """
    repository = PostgresMemoryRepository(session_factory)

    # 向量库和编码器成对启用，避免出现能写 collection 但不能生成向量的半配置。
    vector_repository: QdrantMemoryRepository | None = None
    encoder: TextMemoryEncoder | None = None
    if qdrant_client is not None and embedding_client is not None:
        resolved_qdrant_config = qdrant_config or settings.qdrant
        vector_repository = QdrantMemoryRepository(
            qdrant_client,
            resolved_qdrant_config,
        )
        encoder = TextMemoryEncoder(
            embedding_client,
            name=settings.embedding.model,
            dimension=resolved_qdrant_config.vector_size,
        )
    elif qdrant_client is not None or embedding_client is not None:
        logger.warning("Memory 向量检索未启用：Qdrant 和 Embedding 必须同时提供")

    # Neo4j 只服务 Semantic Memory，不参与 Episodic 或 Perceptual Memory。
    graph_repository: Neo4jGraphRepository | None = None
    graph_schema_ready = False
    if neo4j_driver is not None:
        resolved_neo4j_config = neo4j_config or settings.neo4j
        graph_repository = Neo4jGraphRepository(
            neo4j_driver,
            resolved_neo4j_config,
        )
        if initialize_graph_schema:
            try:
                await ensure_graph_schema(
                    neo4j_driver,
                    resolved_neo4j_config.database,
                )
                graph_schema_ready = True
            except Exception:
                logger.exception(
                    "Neo4j Memory Schema 初始化失败，图投影状态将保留为降级状态"
                )
                if strict_graph_schema:
                    raise

    episodic = EpisodicMemory(
        repository=repository,
        vector_repository=vector_repository,
        encoder=encoder,
    )
    semantic = SemanticMemory(
        repository=repository,
        vector_repository=vector_repository,
        encoder=encoder,
        graph_repository=graph_repository,
    )
    perceptual = PerceptualMemory(
        repository=repository,
        vector_repository=vector_repository,
        encoder=encoder,
    )
    # Working Memory 与 Harness 共用 PostgreSQL 的会话消息表。
    working = WorkingMemory(session_factory)
    manager = MemoryManager(
        working=working,
        episodic=episodic,
        semantic=semantic,
        perceptual=perceptual,
        repository=repository,
    )
    formation_service = MemoryFormationService(
        repository=repository,
        governance=MemoryGovernance(repository),
        writer=MemoryWriter(manager),
        llm_extractor=(
            LlmMemoryExtractor(llm_client) if llm_client is not None else None
        ),
        eligibility=MemoryEligibilityEvaluator(
            automatic_formation_enabled=llm_client is not None
        ),
    )
    lifecycle = MemoryLifecycle(
        repository=repository,
        memories={
            MemoryType.EPISODIC: episodic,
            MemoryType.SEMANTIC: semantic,
            MemoryType.PERCEPTUAL: perceptual,
        },
    )
    return MemoryRuntime(
        manager=manager,
        lifecycle=lifecycle,
        vector_enabled=vector_repository is not None,
        graph_enabled=graph_repository is not None,
        graph_schema_ready=graph_schema_ready,
        formation_service=formation_service,
    )


__all__ = ["MemoryRuntime", "build_memory_runtime"]
