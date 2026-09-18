"""Harness 工具结果 Artifact 的 PostgreSQL 持久化实现。

实现 ``ResultArtifactStore`` 接口（契约见 app/agent/tool_runtime/artifacts.py）。
关键机制：
- 内容寻址：artifact_id = SHA-256(run_id:action_id:payload_hash)，重放同请求得同一引用
- 身份边界：写入校验 run 存在且身份一致；读取按完整五元组过滤，裸 result_ref 不能越权
- 幂等写：同 (run_id, action_id) 已存在时，内容一致幂等返回，不一致报冲突
- 生命周期：写入时打 expires_at（默认 30 天），读取时校验过期
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.tool_runtime.artifacts import (
    ArtifactReadRequest,
    ArtifactRecord,
    ArtifactStoreError,
    ArtifactWriteRequest,
    ResultArtifactStore,
)
from app.models.harness import HarnessArtifactModel, HarnessRunModel


def _utcnow() -> datetime:
    """统一取 UTC naive 当前时间，对齐数据库无时区列。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _payload_bytes(payload: dict) -> bytes:
    """规范 JSON 序列化（键排序 + 紧凑分隔符），保证同一内容的哈希逐字节一致。"""
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactStoreError(
            "artifact_not_json_serializable",
            "工具结果不能序列化为 JSON",
            retryable=False,
        ) from exc


class PostgresResultArtifactStore(ResultArtifactStore):
    """按运行身份持久化和读取工具结果。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_payload_bytes: int = 16 * 1024 * 1024,  # 单个 Artifact 的字节上限
        retention_days: int = 30,  # 保留天数：写入时算 expires_at，读取时校验
    ) -> None:
        if max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes 必须大于 0")
        if retention_days <= 0:
            raise ValueError("retention_days 必须大于 0")
        self.session_factory = session_factory
        self.max_payload_bytes = max_payload_bytes
        self.retention_days = retention_days

    @staticmethod
    def _assert_run(run: HarnessRunModel | None, request: ArtifactWriteRequest) -> None:
        """写入前身份校验：run 必须存在，且会话/线程/轮次与请求 run_ref 完全一致。"""
        if run is None:
            raise ArtifactStoreError(
                "artifact_run_not_found", "Harness run 不存在", retryable=False
            )
        for field in ("user_id", "conversation_id", "thread_id", "turn_id"):
            if getattr(run, field) != getattr(request.run_ref, field):
                raise ArtifactStoreError(
                    "artifact_run_identity_mismatch",
                    f"Artifact 运行身份不匹配: {field}",
                    retryable=False,
                )

    @staticmethod
    def _record(model: HarnessArtifactModel) -> ArtifactRecord:
        """ORM 行转领域记录；时间列补回 UTC 时区。"""
        return ArtifactRecord(
            artifact_id=model.artifact_id,
            result_ref=model.result_ref,
            run_ref={
                "user_id": model.user_id,
                "conversation_id": model.conversation_id,
                "thread_id": model.thread_id,
                "turn_id": model.turn_id,
                "run_id": model.run_id,
            },
            action_id=model.action_id,
            tool_name=model.tool_name,
            artifact_kind=model.artifact_kind,
            payload=dict(model.payload),
            payload_hash=model.payload_hash,
            size_bytes=model.size_bytes,
            created_at=model.created_at.replace(tzinfo=UTC),
            expires_at=model.expires_at.replace(tzinfo=UTC),
        )

    async def save(self, request: ArtifactWriteRequest) -> ArtifactRecord:
        """保存工具结果；同一动作内容一致则幂等返回，不一致则拒绝覆盖。"""
        payload_bytes = _payload_bytes(request.payload)
        if len(payload_bytes) > self.max_payload_bytes:
            raise ArtifactStoreError(
                "artifact_too_large",
                f"工具结果超过 {self.max_payload_bytes} 字节限制",
                retryable=False,
            )
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        # 内容寻址 + 动作绑定：同 run 同动作同内容必得同一 artifact_id/result_ref
        artifact_id = hashlib.sha256(
            f"{request.run_ref.run_id}:{request.action_id}:{payload_hash}".encode("utf-8")
        ).hexdigest()
        result_ref = f"artifact:{artifact_id}"
        now = _utcnow()

        try:
            async with self.session_factory() as session:
                run = await session.scalar(
                    select(HarnessRunModel)
                    .where(HarnessRunModel.run_id == request.run_ref.run_id)
                    .with_for_update()  # 行锁：串行化同 run 的并发写入与幂等判断
                )
                self._assert_run(run, request)
                # 幂等分支：同 (run_id, action_id) 已存在 → 内容一致原样返回，不一致报冲突
                existing = await session.scalar(
                    select(HarnessArtifactModel).where(
                        HarnessArtifactModel.run_id == request.run_ref.run_id,
                        HarnessArtifactModel.action_id == request.action_id,
                    )
                )
                if existing is not None:
                    if (
                        existing.payload_hash != payload_hash
                        or existing.tool_name != request.tool_name
                        or existing.artifact_kind != request.artifact_kind
                    ):
                        raise ArtifactStoreError(
                            "artifact_action_conflict",
                            "同一工具动作已经保存了不同结果",
                            retryable=False,
                        )
                    return self._record(existing)

                model = HarnessArtifactModel(
                    artifact_id=artifact_id,
                    result_ref=result_ref,
                    run_id=request.run_ref.run_id,
                    user_id=request.run_ref.user_id,
                    conversation_id=request.run_ref.conversation_id,
                    thread_id=request.run_ref.thread_id,
                    turn_id=request.run_ref.turn_id,
                    action_id=request.action_id,
                    tool_name=request.tool_name,
                    artifact_kind=request.artifact_kind,
                    payload=request.payload,
                    payload_hash=payload_hash,
                    size_bytes=len(payload_bytes),
                    created_at=now,
                    expires_at=now + timedelta(days=self.retention_days),
                )
                session.add(model)
                await session.commit()
                return self._record(model)
        except ArtifactStoreError:
            raise
        except IntegrityError as exc:
            # 唯一键并发冲突：标记可重试，由工具重试预算兜底
            raise ArtifactStoreError(
                "artifact_conflict",
                "Artifact 并发写入冲突",
                retryable=True,
            ) from exc
        except DBAPIError as exc:
            raise ArtifactStoreError(
                "artifact_database_unavailable",
                "Artifact 数据库暂时不可用",
                retryable=True,
            ) from exc

    async def read(self, request: ArtifactReadRequest) -> ArtifactRecord:
        """按完整运行身份读取；result_ref 只是定位符，不是授权凭证。"""
        try:
            async with self.session_factory() as session:
                model = await session.scalar(
                    select(HarnessArtifactModel).where(
                        # 五元组全部入 WHERE：拿到别的运行留下的 result_ref 也读不到
                        HarnessArtifactModel.result_ref == request.result_ref,
                        HarnessArtifactModel.run_id == request.run_ref.run_id,
                        HarnessArtifactModel.user_id == request.run_ref.user_id,
                        HarnessArtifactModel.conversation_id
                        == request.run_ref.conversation_id,
                        HarnessArtifactModel.thread_id == request.run_ref.thread_id,
                        HarnessArtifactModel.turn_id == request.run_ref.turn_id,
                    )
                )
                if model is None:
                    raise ArtifactStoreError(
                        "artifact_not_found",
                        "Artifact 不存在或不属于当前运行",
                        retryable=False,
                    )
                if model.expires_at <= _utcnow():
                    raise ArtifactStoreError(
                        "artifact_expired", "Artifact 已过期", retryable=False
                    )
                return self._record(model)
        except ArtifactStoreError:
            raise
        except DBAPIError as exc:
            raise ArtifactStoreError(
                "artifact_database_unavailable",
                "Artifact 数据库暂时不可用",
                retryable=True,
            ) from exc


__all__ = ["PostgresResultArtifactStore"]
