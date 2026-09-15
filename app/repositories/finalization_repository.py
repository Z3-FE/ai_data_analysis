"""Finalization Ledger 的 PostgreSQL 持久化。

收口进度只沿 prepared → history_saved → checkpoint_saved → released →
formation_submitted → completed 单向推进；每一步收口操作本身幂等，
Ledger 负责"进行到哪一步"与"锁定收口内容"。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop_controller.contracts import FinalizationInput
from app.models.harness import HarnessFinalizationModel


class FinalizationLedgerError(ValueError):
    """Ledger 状态或收口内容冲突。"""


class FinalizationDigestConflict(FinalizationLedgerError):
    """同一 run 试图用不同内容重复收口。"""


_STAGE_ORDER = (
    "prepared",
    "history_saved",
    "checkpoint_saved",
    "released",
    "formation_submitted",
    "completed",
)


def stage_rank(stage: str) -> int:
    """返回阶段序号；未知阶段直接拒绝。"""
    try:
        return _STAGE_ORDER.index(stage)
    except ValueError as exc:
        raise FinalizationLedgerError(f"未知的收口阶段: {stage}") from exc


class PostgresFinalizationLedger:
    """使用现有 Agent PostgreSQL 连接池维护收口账本。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _input_payload(value: FinalizationInput) -> dict[str, Any]:
        """保存受控收口输入；reconcile 只凭这份 JSON 重建输入。"""
        return value.model_dump(mode="json")

    async def prepare(
        self,
        value: FinalizationInput,
        digest: str,
    ) -> HarnessFinalizationModel:
        """创建账本行并锁定收口内容；同 run 重复收口必须命中同一 digest。"""
        async with self.session_factory() as session:
            model = HarnessFinalizationModel(
                run_id=value.run_ref.run_id,
                user_id=value.run_ref.user_id,
                conversation_id=value.run_ref.conversation_id,
                turn_id=value.run_ref.turn_id,
                finalization_digest=digest,
                terminal_status=value.terminal_status.value,
                stage="prepared",
                final_answer=value.final_answer,
                error_message=value.error_message,
                input_payload=self._input_payload(value),
            )
            session.add(model)
            try:
                await session.commit()
                return model
            except IntegrityError:
                await session.rollback()
            existing = await self.get(
                run_id=value.run_ref.run_id, user_id=value.run_ref.user_id
            )
            if existing is None:
                raise FinalizationLedgerError("收口账本创建冲突后无法读取已有记录")
            if existing.finalization_digest != digest:
                raise FinalizationDigestConflict(
                    "同一运行不允许使用不同内容重复收口: "
                    f"run_id={value.run_ref.run_id}"
                )
            return existing

    async def get(
        self, *, run_id: str, user_id: str
    ) -> HarnessFinalizationModel | None:
        """按运行身份读取账本行。"""
        async with self.session_factory() as session:
            return await session.scalar(
                select(HarnessFinalizationModel).where(
                    HarnessFinalizationModel.run_id == run_id,
                    HarnessFinalizationModel.user_id == user_id,
                )
            )

    async def advance(
        self,
        *,
        run_id: str,
        user_id: str,
        stage: str,
        formation_run_id: str | None = None,
        last_error: dict[str, Any] | None = None,
    ) -> HarnessFinalizationModel:
        """把账本推进到下一阶段；后退或跳跃都表示调用方违反固定顺序。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(HarnessFinalizationModel)
                .where(
                    HarnessFinalizationModel.run_id == run_id,
                    HarnessFinalizationModel.user_id == user_id,
                )
                .with_for_update()
            )
            if model is None:
                raise FinalizationLedgerError(f"收口账本不存在: run_id={run_id}")
            if stage_rank(stage) != stage_rank(model.stage) + 1:
                raise FinalizationLedgerError(
                    f"非法收口阶段推进: {model.stage} -> {stage}"
                )
            model.stage = stage
            if formation_run_id is not None:
                model.formation_run_id = formation_run_id
            if last_error is not None:
                model.last_error = last_error
            if stage == "completed":
                model.attempts += 1
            await session.commit()
            return model

    async def mark_reconcile_attempt(self, *, run_id: str, user_id: str) -> None:
        """记录一次对账重放。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(HarnessFinalizationModel)
                .where(
                    HarnessFinalizationModel.run_id == run_id,
                    HarnessFinalizationModel.user_id == user_id,
                )
                .with_for_update()
            )
            if model is not None:
                model.attempts += 1
                await session.commit()


__all__ = [
    "FinalizationDigestConflict",
    "FinalizationLedgerError",
    "PostgresFinalizationLedger",
]
