"""ContextEngine 的 PostgreSQL 摘要和审计仓储。"""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context_engine.contracts import (
    ContextBuildTrace,
    ContextConversationSummary,
    ContextRequest,
)
from app.models.context_engine import (
    ContextBuildRunModel,
    ContextConversationSummaryModel,
)
from app.models.agent_history import ConversationModel


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _json_safe(value: Any) -> Any:
    """把 dataclass、枚举和时间转换成 PostgreSQL JSONB 可写结构。"""

    def default(item: Any) -> Any:
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, datetime):
            return item.isoformat()
        return str(item)

    return json.loads(json.dumps(value, ensure_ascii=False, default=default))


def _summary(model: ContextConversationSummaryModel) -> ContextConversationSummary:
    return ContextConversationSummary(
        summary_id=model.summary_id,
        user_id=model.user_id,
        conversation_id=model.conversation_id,
        content=model.content,
        covered_from_index=model.covered_from_index,
        covered_through_index=model.covered_through_index,
        source_message_count=model.source_message_count,
        token_count=model.token_count,
        version=model.version,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class PostgresContextStore:
    """持久化增量摘要和不含正文的上下文构建 trace。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def get_summary(
        self, user_id: str, conversation_id: str
    ) -> ContextConversationSummary | None:
        async with self.session_factory() as session:
            model = await session.scalar(
                select(ContextConversationSummaryModel).where(
                    ContextConversationSummaryModel.user_id == user_id,
                    ContextConversationSummaryModel.conversation_id == conversation_id,
                )
            )
            return _summary(model) if model is not None else None

    async def save_summary(
        self, summary: ContextConversationSummary
    ) -> ContextConversationSummary:
        """原子 upsert；并发时只允许更大的消息覆盖范围替代旧摘要。"""
        now = _utcnow()
        table = ContextConversationSummaryModel
        statement = insert(table).values(
            summary_id=summary.summary_id,
            user_id=summary.user_id,
            conversation_id=summary.conversation_id,
            content=summary.content,
            covered_from_index=summary.covered_from_index,
            covered_through_index=summary.covered_through_index,
            source_message_count=summary.source_message_count,
            token_count=summary.token_count,
            version=1,
            created_at=(summary.created_at or now).replace(tzinfo=None),
            updated_at=now,
        )
        statement = statement.on_conflict_do_update(
            constraint="uk_context_summary_user_conversation",
            set_={
                "content": statement.excluded.content,
                "covered_from_index": statement.excluded.covered_from_index,
                "covered_through_index": statement.excluded.covered_through_index,
                "source_message_count": statement.excluded.source_message_count,
                "token_count": statement.excluded.token_count,
                "version": table.version + 1,
                "updated_at": statement.excluded.updated_at,
            },
            where=(
                statement.excluded.covered_through_index > table.covered_through_index
            ),
        ).returning(table)
        async with self.session_factory() as session:
            await self._ensure_conversation_access(
                session,
                user_id=summary.user_id,
                conversation_id=summary.conversation_id,
            )
            model = await session.scalar(statement)
            await session.commit()
            if model is None:
                model = await session.scalar(
                    select(table).where(
                        table.user_id == summary.user_id,
                        table.conversation_id == summary.conversation_id,
                    )
                )
            if model is None:
                raise RuntimeError("会话摘要保存后无法读取")
            return _summary(model)

    async def start_build(
        self,
        *,
        build_id: str,
        request: ContextRequest,
        token_budget: int,
        query_hash: str,
    ) -> None:
        model = ContextBuildRunModel(
            build_id=build_id,
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            agent_type=request.agent_type,
            status="pending",
            query_hash=query_hash,
            token_budget=token_budget,
        )
        async with self.session_factory() as session:
            await self._ensure_conversation_access(
                session,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
            )
            session.add(model)
            await session.commit()

    @staticmethod
    async def _ensure_conversation_access(
        session: AsyncSession, *, user_id: str, conversation_id: str
    ) -> None:
        """拒绝为不存在或属于其他用户的会话写入上下文数据。"""
        exists = await session.scalar(
            select(ConversationModel.conversation_id).where(
                ConversationModel.conversation_id == conversation_id,
                ConversationModel.user_id == user_id,
            )
        )
        if exists is None:
            raise PermissionError("会话不存在或当前用户无权访问")

    async def finish_build(self, trace: ContextBuildTrace) -> None:
        values = {
            "status": trace.status,
            "final_token_count": trace.final_token_count,
            "candidate_count": trace.candidate_count,
            "selected_count": trace.selected_count,
            "retrieval_plan": _json_safe(asdict(trace.retrieval_plan)),
            "reference_resolution": _json_safe(asdict(trace.reference_resolution)),
            "decisions": _json_safe([asdict(decision) for decision in trace.decisions]),
            "summary_updated": trace.summary_updated,
            "error_message": trace.error_message or None,
            "completed_at": _utcnow(),
        }
        async with self.session_factory() as session:
            await session.execute(
                update(ContextBuildRunModel)
                .where(ContextBuildRunModel.build_id == trace.build_id)
                .values(**values)
            )
            await session.commit()

    async def fail_build(self, build_id: str, *, error_message: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(ContextBuildRunModel)
                .where(ContextBuildRunModel.build_id == build_id)
                .values(
                    status="failed",
                    error_message=error_message[:4000],
                    completed_at=_utcnow(),
                )
            )
            await session.commit()


__all__ = ["PostgresContextStore"]
