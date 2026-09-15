"""Finalization 真实 PostgreSQL 验收共享的连接、建表与清理工具。

这些工具只服务验收测试；连接的是本地 agent_app 库，不是进程内替身。
"""

from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.agent.memory.formation_service import MemoryFormationService
from app.agent.memory.governance import MemoryGovernance
from app.agent.memory.manager import MemoryManager
from app.agent.memory.types.episodic import EpisodicMemory
from app.agent.memory.types.perceptual import PerceptualMemory
from app.agent.memory.types.semantic import SemanticMemory
from app.agent.memory.types.working import WorkingMemory
from app.agent.memory.writer import MemoryWriter
from app.core.config import settings
from app.models.agent_history import (
    ConversationMessageModel,
    ConversationModel,
    ConversationTurnModel,
    TurnOutputModel,
)
from app.models.base import Base
from app.models.context_engine import ContextBuildRunModel, ContextConversationSummaryModel
from app.models.harness import (
    HarnessActionModel,
    HarnessArtifactModel,
    HarnessConfirmationModel,
    HarnessFinalizationModel,
    HarnessRunModel,
)
from app.models.memory import MemoryFormationRunModel
from app.repositories.memory.postgres_memory_repository import PostgresMemoryRepository

# 导入模型模块即完成 Base 注册；MySQL Meta ORM 使用独立的 Base，不会进入这里。
_BASE_MODELS = (
    ConversationModel,
    ConversationTurnModel,
    ConversationMessageModel,
    TurnOutputModel,
    ContextConversationSummaryModel,
    ContextBuildRunModel,
    HarnessRunModel,
    HarnessActionModel,
    HarnessConfirmationModel,
    HarnessArtifactModel,
    HarnessFinalizationModel,
    MemoryFormationRunModel,
)


def create_session_factory() -> tuple[AsyncEngine, async_sessionmaker]:
    """创建指向本地 agent_app 库的真实连接池。"""
    engine = create_async_engine(settings.postgres.database_url)
    return engine, async_sessionmaker(engine, autoflush=True, expire_on_commit=False)


async def ensure_tables(engine: AsyncEngine) -> None:
    """按 ORM 模型补齐缺失表；已存在的表不受影响。"""
    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            tables=[model.__table__ for model in _BASE_MODELS],
        )


async def cleanup_user(session_factory: async_sessionmaker, user_id: str) -> None:
    """删除测试用户的会话、运行和形成审计；账本随 run 级联删除。"""
    async with session_factory() as session:
        conversation_ids = (
            await session.scalars(
                select(ConversationModel.conversation_id).where(
                    ConversationModel.user_id == user_id
                )
            )
        ).all()
        if conversation_ids:
            await session.execute(
                delete(TurnOutputModel).where(
                    TurnOutputModel.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(ConversationTurnModel).where(
                    ConversationTurnModel.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(ConversationModel).where(
                    ConversationModel.conversation_id.in_(conversation_ids)
                )
            )
        await session.execute(
            delete(MemoryFormationRunModel).where(
                MemoryFormationRunModel.user_id == user_id
            )
        )
        await session.execute(
            delete(HarnessRunModel).where(HarnessRunModel.user_id == user_id)
        )
        await session.commit()


def new_run_identity(prefix: str) -> dict[str, str]:
    """生成互不冲突的测试运行身份。"""
    suffix = uuid4().hex[:12]
    return {
        "user_id": f"{prefix}-user-{suffix}",
        "conversation_id": f"{prefix}-conv-{suffix}",
        "thread_id": f"{prefix}-conv-{suffix}",
        "turn_id": f"{prefix}-turn-{suffix}",
        "run_id": f"{prefix}-run-{suffix}",
    }


def build_formation_service(session_factory: async_sessionmaker) -> MemoryFormationService:
    """组装不依赖 Qdrant、Neo4j 和 LLM 的真实记忆形成服务。

    自动形成关闭时，Harness 完成轮次会得到 skipped 形成审计，
    但形成任务本身仍然真实落库，供 Ledger 记录 formation_run_id。
    """
    repository = PostgresMemoryRepository(session_factory)
    manager = MemoryManager(
        working=WorkingMemory(session_factory),
        episodic=EpisodicMemory(repository=repository),
        semantic=SemanticMemory(repository=repository),
        perceptual=PerceptualMemory(repository=repository),
        repository=repository,
    )
    return MemoryFormationService(
        repository=repository,
        governance=MemoryGovernance(repository),
        writer=MemoryWriter(manager),
        llm_extractor=None,
    )
