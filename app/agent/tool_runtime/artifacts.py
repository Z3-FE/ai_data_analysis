"""Harness 工具结果 Artifact 的跨模块契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import Field

from app.agent.state_result_store.contracts import ContractModel, HarnessRunRef


class ArtifactWriteRequest(ContractModel):
    """把一个已完成工具动作的完整结果保存到受控存储。"""

    run_ref: HarnessRunRef
    action_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)
    artifact_kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]


class ArtifactReadRequest(ContractModel):
    """按完整运行身份读取结果，禁止只凭 result_ref 越权访问。"""

    run_ref: HarnessRunRef
    result_ref: str = Field(min_length=1, max_length=256)


class ArtifactRecord(ContractModel):
    """ArtifactStore 返回的可验证结果记录。"""

    artifact_id: str = Field(min_length=64, max_length=64)
    result_ref: str = Field(min_length=1, max_length=256)
    run_ref: HarnessRunRef
    action_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=128)
    artifact_kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]
    payload_hash: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    created_at: datetime
    expires_at: datetime


class ArtifactStoreError(RuntimeError):
    """Artifact 持久化失败，并显式标明是否允许控制器重试。"""

    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class ResultArtifactStore(Protocol):
    async def save(self, request: ArtifactWriteRequest) -> ArtifactRecord: ...

    async def read(self, request: ArtifactReadRequest) -> ArtifactRecord: ...


__all__ = [
    "ArtifactReadRequest",
    "ArtifactRecord",
    "ArtifactStoreError",
    "ArtifactWriteRequest",
    "ResultArtifactStore",
]
