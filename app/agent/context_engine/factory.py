"""ContextEngine 的独立依赖组装入口。"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context_engine.compiler import ContextCompiler
from app.agent.context_engine.compressor import TokenBoundaryCompressor
from app.agent.context_engine.contracts import ContextPolicy
from app.agent.context_engine.deduplicator import ContextDeduplicator
from app.agent.context_engine.engine import ContextEngine
from app.agent.context_engine.history import ConversationHistoryManager
from app.agent.context_engine.interfaces import (
    ContextKnowledgeRetriever,
    ContextPlanner,
    ConversationSummarizer,
    MemoryContextReader,
)
from app.agent.context_engine.planner import (
    DeterministicContextPlanner,
    LlmContextPlanner,
)
from app.agent.context_engine.resolver import ContextReferenceResolver
from app.agent.context_engine.selector import ContextSelector
from app.agent.context_engine.summarizer import (
    DeterministicConversationSummarizer,
    LlmConversationSummarizer,
)
from app.agent.context_engine.token_counter import TiktokenCounter
from app.repositories.context_repository import PostgresContextRepository


def build_context_engine(
    *,
    memory_reader: MemoryContextReader,
    session_factory: async_sessionmaker[AsyncSession],
    llm_client: Any = None,
    policy: ContextPolicy | None = None,
    planner: ContextPlanner | None = None,
    summarizer: ConversationSummarizer | None = None,
    knowledge_retriever: ContextKnowledgeRetriever | None = None,
    model_name: str | None = None,
) -> ContextEngine:
    """组装独立 ContextEngine；当前函数不会修改或接入 Agent 图。"""
    resolved_policy = policy or ContextPolicy()
    token_counter = TiktokenCounter(model_name=model_name)
    context_repository = PostgresContextRepository(session_factory)
    fallback_summarizer = DeterministicConversationSummarizer(token_counter)
    resolved_summarizer = summarizer or (
        LlmConversationSummarizer(
            llm_client,
            token_counter,
            fallback=fallback_summarizer,
        )
        if llm_client is not None
        else fallback_summarizer
    )
    fallback_planner = DeterministicContextPlanner()
    resolved_planner = planner or (
        LlmContextPlanner(llm_client, fallback=fallback_planner)
        if llm_client is not None
        else fallback_planner
    )
    resolver = ContextReferenceResolver(memory_reader)
    history = ConversationHistoryManager(
        context_repository=context_repository,
        summarizer=resolved_summarizer,
        token_counter=token_counter,
        policy=resolved_policy,
    )
    compressor = TokenBoundaryCompressor(token_counter)
    selector = ContextSelector(
        policy=resolved_policy,
        token_counter=token_counter,
        compressor=compressor,
    )
    return ContextEngine(
        memory_reader=memory_reader,
        context_repository=context_repository,
        planner=resolved_planner,
        resolver=resolver,
        history=history,
        deduplicator=ContextDeduplicator(),
        selector=selector,
        compiler=ContextCompiler(token_counter),
        token_counter=token_counter,
        policy=resolved_policy,
        knowledge_retriever=knowledge_retriever,
    )


__all__ = ["build_context_engine"]
