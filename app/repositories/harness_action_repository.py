"""Harness 动作的 PostgreSQL 提交实现。"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop_controller.action_commit import (
    ActionCommitRequest,
    ActionCommitResult,
    ActionCommitter,
)
from app.models.harness import HarnessActionModel, HarnessRunModel
from app.repositories.harness_run_repository import HarnessPersistenceError


def _action_digest(request: ActionCommitRequest) -> tuple[dict, str]:
    """生成不含模型隐藏内容的动作载荷和摘要。"""
    payload = request.action.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload, digest


class PostgresActionCommitter(ActionCommitter):
    """在工具或确认执行前持久化已校验的 NextAction。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def commit(self, request: ActionCommitRequest) -> ActionCommitResult:
        payload, payload_digest = _action_digest(request)
        async with self.session_factory() as session:
            run = await session.scalar(
                select(HarnessRunModel)
                .where(
                    HarnessRunModel.run_id == request.run_ref.run_id,
                    HarnessRunModel.user_id == request.run_ref.user_id,
                )
                .with_for_update()
            )
            if run is None:
                raise HarnessPersistenceError("Harness run 不存在")
            for field in ("conversation_id", "thread_id", "turn_id"):
                if getattr(run, field) != getattr(request.run_ref, field):
                    raise HarnessPersistenceError(f"Harness 动作身份不匹配: {field}")
            if request.expected_action_seq != run.action_seq + 1:
                existing = await session.scalar(
                    select(HarnessActionModel).where(
                        HarnessActionModel.run_id == request.run_ref.run_id,
                        HarnessActionModel.action_seq == request.expected_action_seq,
                    )
                )
                if existing is not None:
                    if (
                        existing.payload_digest != payload_digest
                        or existing.commit_stage != "committed"
                    ):
                        raise HarnessPersistenceError("同一动作序号存在冲突内容")
                    return self._result(request, status="idempotent")
                raise HarnessPersistenceError("动作序号与当前运行不连续")

            if run.state_version != request.expected_state_version:
                raise HarnessPersistenceError("动作基于过期的 Harness 状态")

            existing = await session.scalar(
                select(HarnessActionModel).where(
                    HarnessActionModel.run_id == request.run_ref.run_id,
                    HarnessActionModel.action_seq == request.expected_action_seq,
                )
            )
            if existing is not None:
                if (
                    existing.payload_digest != payload_digest
                    or existing.commit_stage != "committed"
                ):
                    raise HarnessPersistenceError("同一动作序号存在冲突内容")
                return self._result(request, status="idempotent")

            action_id = (
                request.action.tool_call.action_id
                if request.action.tool_call is not None
                else None
            )
            session.add(
                HarnessActionModel(
                    action_record_id=(
                        f"{request.run_ref.run_id}:action:{request.action.action_seq}"
                    ),
                    run_id=request.run_ref.run_id,
                    action_seq=request.action.action_seq,
                    action_id=action_id,
                    action_type=request.action.action_type.value,
                    action_payload=payload,
                    payload_digest=payload_digest,
                    commit_stage="committed",
                )
            )
            # 与查询列同步更新 JSON 现场，避免动作提交后进程中断时出现
            # action_seq 已前进但恢复快照仍是旧值的不一致现场。
            run.action_seq = request.action.action_seq
            payload_state = dict(run.state_payload or {})
            payload_harness = dict(payload_state.get("harness") or {})
            payload_harness["action_seq"] = request.action.action_seq
            payload_state["harness"] = payload_harness
            run.state_payload = payload_state
            await session.commit()
            return self._result(request, status="committed")

    @staticmethod
    def _result(
        request: ActionCommitRequest, *, status: str
    ) -> ActionCommitResult:
        action_id = (
            request.action.tool_call.action_id
            if request.action.tool_call is not None
            else None
        )
        return ActionCommitResult(
            status=status,
            action_seq=request.action.action_seq,
            action_type=request.action.action_type,
            action_id=action_id,
        )


__all__ = ["PostgresActionCommitter"]
