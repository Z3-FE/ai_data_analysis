"""Harness 运行现场和用户确认的 PostgreSQL 持久化。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.loop_controller.contracts import (
    HarnessRunStore,
)
from app.agent.state import HarnessRunState
from app.agent.state_result_store.contracts import (
    ConfirmationRecord,
    ConfirmationReply,
    ConfirmationResolution,
    ConfirmationStatus,
    ConfirmationVisibility,
    HarnessRunRef,
    HarnessStatusType,
    LoopPhaseStatusType,
)
from app.agent.state_result_store.state import (
    decode_harness_state,
    encode_harness_state,
    transition_harness_state,
)
from app.models.harness import (
    HarnessConfirmationModel,
    HarnessRunModel,
)


def _utcnow() -> datetime:
    """返回与当前 PostgreSQL TIMESTAMP 字段一致的无时区 UTC 时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _json_safe(value: Any) -> Any:
    """把状态和回复转换为可写入 JSONB 的值。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _state_payload(state: HarnessRunState) -> dict[str, Any]:
    """只保存可恢复的 JSON-safe Harness 状态。"""
    payload: dict[str, Any] = {}
    for key, value in state.items():
        if key == "harness":
            payload[key] = encode_harness_state(value)
        elif key != "messages":
            payload[key] = _json_safe(value)
    return payload


def _state_from_payload(payload: dict[str, Any]) -> HarnessRunState:
    """读取数据库现场并重新通过 Harness 状态校验。"""
    state = dict(payload)
    state["harness"] = decode_harness_state(state.get("harness", {}))
    return state  # type: ignore[return-value]


def _reply_digest(reply: ConfirmationReply) -> str:
    """计算用户回复摘要，重复请求可以安全识别。"""
    from hashlib import sha256

    return sha256(
        json.dumps(
            reply.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _naive_utc(value: datetime | None) -> datetime | None:
    """把带时区的 API 时间转换为 PostgreSQL 无时区 UTC。"""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class HarnessPersistenceError(ValueError):
    """Harness 持久化状态或并发校验失败。"""


class PostgresHarnessRunStore(HarnessRunStore):
    """使用现有 Agent PostgreSQL 连接池保存 Harness 运行现场。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _assert_ref(model: HarnessRunModel, ref: HarnessRunRef) -> None:
        """确认数据库记录属于当前身份，避免跨用户恢复运行。"""
        for field in ("user_id", "conversation_id", "thread_id", "turn_id", "run_id"):
            if getattr(model, field) != getattr(ref, field):
                raise HarnessPersistenceError(f"Harness 运行身份不匹配: {field}")

    @staticmethod
    def _assert_version(model: HarnessRunModel, state: HarnessRunState) -> None:
        """要求状态版本严格递增，拒绝旧 Worker 覆盖新现场。"""
        candidate = int(state["harness"]["state_version"])
        current = int(model.state_version)
        if candidate == current:
            if model.state_payload != _state_payload(state):
                raise HarnessPersistenceError("Harness 状态版本冲突")
            return
        if candidate != current + 1:
            raise HarnessPersistenceError(
                f"Harness 状态版本不连续: database={current}, candidate={candidate}"
            )

    @staticmethod
    def _apply_state(model: HarnessRunModel, state: HarnessRunState) -> None:
        """把受控状态同步到查询列和完整 JSON 现场。"""
        harness = state["harness"]
        model.status = str(harness["status"])
        model.phase = str(harness["phase"])
        model.action_seq = int(harness["action_seq"])
        model.iteration = int(harness["iteration"])
        pending = harness.get("pending_confirmation")
        model.pending_confirmation_id = (
            str(pending["confirmation_id"]) if isinstance(pending, dict) else None
        )
        model.state_version = int(harness["state_version"])
        model.state_payload = _state_payload(state)
        model.updated_at = _utcnow()

    async def create(self, run_ref: HarnessRunRef, state: HarnessRunState) -> None:
        """创建一条新的 Harness 运行现场。"""
        payload = _state_payload(state)
        harness = state["harness"]
        model = HarnessRunModel(
            run_id=run_ref.run_id,
            user_id=run_ref.user_id,
            conversation_id=run_ref.conversation_id,
            thread_id=run_ref.thread_id,
            turn_id=run_ref.turn_id,
            input_text=state["input_text"],
            project_id=state.get("project_id"),
            asset_ids=list(state.get("asset_ids", ())),
            status=str(harness["status"]),
            phase=str(harness["phase"]),
            action_seq=int(harness["action_seq"]),
            iteration=int(harness["iteration"]),
            state_version=int(harness["state_version"]),
            pending_confirmation_id=None,
            state_payload=payload,
        )
        async with self.session_factory() as session:
            session.add(model)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise HarnessPersistenceError("Harness run 已存在") from exc

    async def _locked_model(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        user_id: str,
    ) -> HarnessRunModel:
        """锁定运行行，序列化同一 run 的保存和恢复。"""
        model = await session.scalar(
            select(HarnessRunModel)
            .where(
                HarnessRunModel.run_id == run_id,
                HarnessRunModel.user_id == user_id,
            )
            .with_for_update()
        )
        if model is None:
            raise HarnessPersistenceError("Harness run 不存在")
        return model

    async def load(self, run_ref: HarnessRunRef) -> HarnessRunState:
        """按完整运行身份读取现场。"""
        async with self.session_factory() as session:
            model = await self._locked_model(
                session, run_id=run_ref.run_id, user_id=run_ref.user_id
            )
            self._assert_ref(model, run_ref)
            return _state_from_payload(dict(model.state_payload))

    async def load_by_id(self, *, run_id: str, user_id: str) -> HarnessRunState:
        """按用户隔离读取运行现场，供状态查询和恢复入口使用。"""
        async with self.session_factory() as session:
            model = await self._locked_model(session, run_id=run_id, user_id=user_id)
            return _state_from_payload(dict(model.state_payload))

    async def save(self, run_ref: HarnessRunRef, state: HarnessRunState) -> None:
        """以连续 state_version 保存普通运行阶段。"""
        async with self.session_factory() as session:
            model = await self._locked_model(
                session, run_id=run_ref.run_id, user_id=run_ref.user_id
            )
            self._assert_ref(model, run_ref)
            self._assert_version(model, state)
            self._apply_state(model, state)
            await session.commit()

    async def pause_for_confirmation(
        self,
        run_ref: HarnessRunRef,
        state: HarnessRunState,
        record: ConfirmationRecord,
    ) -> None:
        """在一个 PostgreSQL 事务内发布确认请求并保存等待现场。"""
        if record.run_ref != run_ref:
            raise HarnessPersistenceError("确认记录与运行身份不一致")
        if record.status is not ConfirmationStatus.PENDING:
            raise HarnessPersistenceError("新确认记录必须处于 pending")
        if record.visibility is not ConfirmationVisibility.PUBLISHED:
            raise HarnessPersistenceError("只有 published 确认请求可以进入等待状态")
        async with self.session_factory() as session:
            model = await self._locked_model(
                session, run_id=run_ref.run_id, user_id=run_ref.user_id
            )
            self._assert_ref(model, run_ref)
            self._assert_version(model, state)
            harness = state["harness"]
            if harness["status"] != HarnessStatusType.WAITING_CONFIRMATION.value:
                raise HarnessPersistenceError("只有 waiting_confirmation 状态可以暂停")
            pending = harness.get("pending_confirmation")
            if not isinstance(pending, dict) or pending.get("confirmation_id") != record.request.confirmation_id:
                raise HarnessPersistenceError("运行现场与确认请求不匹配")
            existing = await session.scalar(
                select(HarnessConfirmationModel)
                .where(HarnessConfirmationModel.confirmation_id == record.request.confirmation_id)
                .with_for_update()
            )
            if existing is not None:
                if existing.request_digest != record.request_digest:
                    raise HarnessPersistenceError("确认请求摘要冲突")
            else:
                session.add(
                    HarnessConfirmationModel(
                        confirmation_id=record.request.confirmation_id,
                        run_id=run_ref.run_id,
                        action_seq=int(harness["action_seq"]),
                        user_id=run_ref.user_id,
                        conversation_id=run_ref.conversation_id,
                        question=record.request.question,
                        reason_code=record.request.reason_code,
                        required_fields=list(record.request.required_fields),
                        choices=list(record.request.choices),
                        request_payload=record.request.model_dump(mode="json"),
                        request_digest=record.request_digest,
                        status=record.status.value,
                        visibility=record.visibility.value,
                        expires_at=_naive_utc(record.request.expires_at),
                        prepared_at=_naive_utc(record.prepared_at),
                        published_at=_naive_utc(record.published_at),
                    )
                )
            self._apply_state(model, state)
            await session.commit()

    @staticmethod
    def _validate_reply(record: HarnessConfirmationModel, reply: ConfirmationReply) -> None:
        """校验回复身份、过期时间和条件字段白名单。"""
        if record.visibility != ConfirmationVisibility.PUBLISHED.value:
            raise HarnessPersistenceError("确认请求尚未发布")
        if record.expires_at is not None and record.expires_at <= _utcnow():
            raise HarnessPersistenceError("确认请求已过期")
        allowed = set(record.required_fields or [])
        forbidden = {"user_id", "conversation_id", "thread_id", "turn_id", "run_id"}
        if set(reply.resolved_conditions) - allowed:
            raise HarnessPersistenceError("用户回复包含未声明的条件字段")
        if set(reply.resolved_conditions) & forbidden:
            raise HarnessPersistenceError("用户回复不能修改运行身份")

    async def resolve_confirmation(
        self,
        run_ref: HarnessRunRef,
        reply: ConfirmationReply,
    ) -> ConfirmationResolution:
        """原子消费 pending 确认，并返回可继续执行的 Harness 状态。"""
        async with self.session_factory() as session:
            model = await self._locked_model(
                session, run_id=run_ref.run_id, user_id=run_ref.user_id
            )
            self._assert_ref(model, run_ref)
            record = await session.scalar(
                select(HarnessConfirmationModel)
                .where(
                    HarnessConfirmationModel.confirmation_id == reply.confirmation_id,
                    HarnessConfirmationModel.run_id == run_ref.run_id,
                    HarnessConfirmationModel.user_id == run_ref.user_id,
                )
                .with_for_update()
            )
            if record is None:
                raise HarnessPersistenceError("确认记录不存在")
            reply_digest = _reply_digest(reply)
            state = _state_from_payload(dict(model.state_payload))
            if record.status != ConfirmationStatus.PENDING.value:
                if record.reply_digest == reply_digest:
                    return ConfirmationResolution(status="idempotent", state=state)
                raise HarnessPersistenceError("确认请求已被不同回复处理")

            harness = state["harness"]
            pending = harness.get("pending_confirmation")
            confirmation_id = (
                pending.get("confirmation_id")
                if isinstance(pending, dict)
                else None
            )
            if harness["status"] != HarnessStatusType.WAITING_CONFIRMATION.value:
                raise HarnessPersistenceError("当前运行不在 waiting_confirmation 状态")
            if confirmation_id != reply.confirmation_id:
                raise HarnessPersistenceError("confirmation_id 与当前运行不匹配")
            self._validate_reply(record, reply)
            harness["confirmation_attempt_count"] = int(
                harness.get("confirmation_attempt_count", 0)
            ) + 1
            harness["last_confirmation_reason_code"] = record.reason_code
            harness["last_confirmation_question"] = record.question
            record.reply_payload = reply.model_dump(mode="json")
            record.reply_digest = reply_digest
            record.status = (
                ConfirmationStatus.CONFIRMED.value
                if reply.decision == "confirm"
                else ConfirmationStatus.REJECTED.value
            )
            record.resolved_at = _utcnow()
            if reply.decision == "confirm":
                harness["last_confirmation_answer"] = reply.answer
                harness["resolved_conditions"] = {
                    **dict(harness.get("resolved_conditions") or {}),
                    **reply.resolved_conditions,
                }
                state = {**state, "harness": harness}
            state["harness"] = transition_harness_state(
                state["harness"],
                status=HarnessStatusType.RUNNING,
                phase=LoopPhaseStatusType.RESTORE_RUN,
            )
            self._apply_state(model, state)
            await session.commit()
            return ConfirmationResolution(
                status=(
                    "confirmed"
                    if reply.decision == "confirm"
                    else "rejected"
                ),
                state=state,
            )


__all__ = ["HarnessPersistenceError", "PostgresHarnessRunStore"]
