"""Harness Tool Runtime 的统一执行和结果归一化。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from time import monotonic

from app.agent.state_result_store.contracts import (
    ErrorCategory,
    LoopPhaseStatusType,
    ResultStatus,
    ToolResult,
)
from app.agent.streaming.contracts import EventType
from app.agent.streaming.writer import HarnessEventWriter, NullHarnessEventWriter
from app.agent.tool_runtime.artifacts import (
    ArtifactReadRequest,
    ArtifactRecord,
    ArtifactStoreError,
    ArtifactWriteRequest,
    ResultArtifactStore,
)
from app.agent.tool_runtime.contracts import ToolExecutionRequest
from app.agent.tool_runtime.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolRuntime:
    """按 ToolSpec 执行注册工具，不按工具名分派。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        artifact_store: ResultArtifactStore | None = None,
        event_writer: HarnessEventWriter | None = None,
    ) -> None:
        self.registry = registry
        # 生产组装层注入 PostgreSQL ArtifactStore；工具自身不接触结果存储。
        self.artifact_store = artifact_store
        self.event_writer = event_writer
        self.calls: list[ToolExecutionRequest] = []

    async def execute(self, request: ToolExecutionRequest) -> ToolResult:
        self.calls.append(request)
        writer = self.event_writer or NullHarnessEventWriter(run_ref=request.run_ref)
        started_at = datetime.now(UTC)
        started = monotonic()
        try:
            spec, tool = self.registry.get(request.tool_call.tool_name)
            value = self._validate_input(tool, request.tool_call.arguments)
        except ValueError as exc:
            result = self._error_result(
                request=request,
                started_at=started_at,
                started=started,
                status=ResultStatus.UNRECOVERABLE_ERROR,
                category=ErrorCategory.VALIDATION,
                code="invalid_tool_input",
                retryable=False,
                error=exc,
            )
            self._publish_result(writer, request, result)
            return result

        try:
            timeout_seconds = (
                request.tool_call.timeout_seconds or spec.timeout_seconds
            )
            writer.emit(
                EventType.TOOL_STARTED,
                source=spec.name,
                phase="execute_tool",
                iteration=request.iteration,
                action_id=request.tool_call.action_id,
                payload={"tool_name": spec.name, "attempt": request.attempt},
            )
            with writer.bind(
                source=spec.name,
                phase=LoopPhaseStatusType.EXECUTE_TOOL,
                iteration=request.iteration,
                action_id=request.tool_call.action_id,
            ):
                output = await asyncio.wait_for(
                    tool.execute(value), timeout=timeout_seconds
                )
            limitations = self._limitations(output)
            result_ref = None
            output_hash = None
            if spec.result_kind in {"artifact", "report"}:
                if self.artifact_store is None:
                    result = self._artifact_error_result(
                        request=request,
                        started_at=started_at,
                        started=started,
                        error=ArtifactStoreError(
                            "artifact_store_not_configured",
                            "工具结果需要配置 ArtifactStore 才能继续",
                            retryable=False,
                        ),
                    )
                    self._publish_result(writer, request, result)
                    return result
                try:
                    artifact = await self.artifact_store.save(
                        ArtifactWriteRequest(
                            run_ref=request.run_ref,
                            action_id=request.tool_call.action_id,
                            tool_name=spec.name,
                            artifact_kind=spec.artifact_kind or f"{spec.name}_result",
                            payload=self._artifact_payload(output),
                        )
                    )
                except ArtifactStoreError as exc:
                    result = self._artifact_error_result(
                        request=request,
                        started_at=started_at,
                        started=started,
                        error=exc,
                    )
                    self._publish_result(writer, request, result)
                    return result
                result_ref = artifact.result_ref
                output_hash = artifact.payload_hash
            result = ToolResult(
                tool_call_id=request.tool_call.action_id,
                tool_name=request.tool_call.tool_name,
                status=(
                    ResultStatus.PARTIAL
                    if limitations
                    else ResultStatus.SUCCESS
                ),
                summary=self._summary(spec.name, output),
                result_ref=result_ref,
                evidence_refs=self._string_list(output, "evidence_refs"),
                limitations=limitations,
                output_hash=output_hash,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                duration_ms=max(0, int((monotonic() - started) * 1000)),
            )
            self._publish_result(writer, request, result)
            return result

        except (TimeoutError, asyncio.TimeoutError) as exc:
            result = self._error_result(
                request=request,
                started_at=started_at,
                started=started,
                status=ResultStatus.UNRECOVERABLE_ERROR,
                category=ErrorCategory.TIMEOUT,
                code="tool_timeout",
                retryable=False,
                error=exc,
            )
            self._publish_result(writer, request, result)
            return result
        except (ConnectionError, OSError) as exc:
            result = self._error_result(
                request=request,
                started_at=started_at,
                started=started,
                status=ResultStatus.UNRECOVERABLE_ERROR,
                category=ErrorCategory.TOOL,
                code="tool_dependency_unavailable",
                retryable=False,
                error=exc,
            )
            self._publish_result(writer, request, result)
            return result
        except Exception as exc:
            result = self._error_result(
                request=request,
                started_at=started_at,
                started=started,
                status=ResultStatus.UNRECOVERABLE_ERROR,
                category=ErrorCategory.TOOL,
                code="tool_execution_failed",
                retryable=False,
                error=exc,
            )
            self._publish_result(writer, request, result)
            return result

    async def read_result(self, request: ArtifactReadRequest) -> ArtifactRecord:
        """通过统一的运行身份边界读取完整结果，不接受裸 result_ref。"""
        if self.artifact_store is None:
            raise ArtifactStoreError(
                "artifact_store_not_configured",
                "当前运行没有配置 ArtifactStore",
                retryable=False,
            )
        return await self.artifact_store.read(request)

    @staticmethod
    def _publish_result(
        writer: HarnessEventWriter,
        request: ToolExecutionRequest,
        result: ToolResult,
    ) -> None:
        """在结果完成或失败后发布受控事件，并记录小体积结构化日志。"""
        terminal_type = (
            EventType.TOOL_COMPLETED
            if result.status in {ResultStatus.SUCCESS, ResultStatus.PARTIAL}
            else EventType.TOOL_FAILED
        )
        writer.emit(
            terminal_type,
            source=request.tool_call.tool_name,
            phase="execute_tool",
            iteration=request.iteration,
            action_id=request.tool_call.action_id,
            payload={
                "tool_name": result.tool_name,
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "result_ref": result.result_ref,
                "error_code": result.error_code,
                "row_count": getattr(result, "row_count", None),
            },
        )
        if result.status not in {ResultStatus.SUCCESS, ResultStatus.PARTIAL}:
            logger.warning(
                "Harness tool failed: run_id=%s action_id=%s tool=%s attempt=%s error_code=%s retryable=%s error=%s",
                request.run_ref.run_id,
                request.tool_call.action_id,
                result.tool_name,
                request.attempt,
                result.error_code,
                result.retryable,
                result.error_message,
                extra={
                    "run_id": request.run_ref.run_id,
                    "turn_id": request.run_ref.turn_id,
                    "action_id": request.tool_call.action_id,
                    "tool_name": result.tool_name,
                    "attempt": request.attempt,
                    "status": result.status.value,
                    "error_code": result.error_code,
                },
            )
        logger.info(
            "Harness tool finished: run_id=%s action_id=%s tool=%s attempt=%s status=%s duration_ms=%s error_code=%s",
            request.run_ref.run_id,
            request.tool_call.action_id,
            result.tool_name,
            request.attempt,
            result.status.value,
            result.duration_ms,
            result.error_code or "",
            extra={
                "run_id": request.run_ref.run_id,
                "turn_id": request.run_ref.turn_id,
                "action_id": request.tool_call.action_id,
                "tool_name": result.tool_name,
                "attempt": request.attempt,
                "status": result.status.value,
                "duration_ms": result.duration_ms,
                "error_code": result.error_code,
            },
        )

    @staticmethod
    def _validate_input(tool, arguments):
        """使用 Registry 已校验的 Pydantic 输入模型。"""
        return tool.input_model.model_validate(arguments)

    @staticmethod
    def _artifact_payload(output) -> dict:
        """把工具完整结果转为 JSON-safe payload，不把完整结果放入观察记录。"""
        model_dump = getattr(output, "model_dump", None)
        if callable(model_dump):
            value = model_dump(mode="json")
        elif isinstance(output, Mapping):
            value = dict(output)
        else:
            raise ArtifactStoreError(
                "artifact_output_not_serializable",
                "工具结果必须提供 Pydantic model_dump 或 Mapping",
                retryable=False,
            )
        if not isinstance(value, dict):
            raise ArtifactStoreError(
                "artifact_output_not_object",
                "工具结果 Artifact 必须是 JSON 对象",
                retryable=False,
            )
        return value

    @staticmethod
    def _summary(tool_name: str, output) -> str:
        summary = getattr(output, "summary", None)
        if isinstance(summary, str) and summary.strip():
            return summary.strip()[:4_000]
        row_count = getattr(output, "row_count", None)
        column_count = getattr(output, "column_count", None)
        if row_count is not None and column_count is not None:
            return f"{tool_name} 执行成功，返回 {row_count} 行、{column_count} 个字段。"
        return f"{tool_name} 执行成功。"

    @staticmethod
    def _artifact_error_result(
        *,
        request: ToolExecutionRequest,
        started_at: datetime,
        started: float,
        error: ArtifactStoreError,
    ) -> ToolResult:
        """Artifact 未成功持久化时禁止返回伪造的工具成功结果。"""
        status = (
            ResultStatus.TEMPORARY_ERROR
            if error.retryable
            else ResultStatus.UNRECOVERABLE_ERROR
        )
        return ToolResult(
            tool_call_id=request.tool_call.action_id,
            tool_name=request.tool_call.tool_name,
            status=status,
            summary="工具结果未能持久化，不能继续使用该结果。",
            error_category=ErrorCategory.DATABASE,
            error_code=error.code,
            error_message=str(error)[:2_000],
            retryable=error.retryable,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            duration_ms=max(0, int((monotonic() - started) * 1000)),
        )

    @staticmethod
    def _limitations(output) -> list[str]:
        values = [
            *(getattr(output, "limitations", ()) or ()),
            *(getattr(output, "mapping_limitations", ()) or ()),
        ]
        return list(dict.fromkeys(str(value) for value in values if str(value).strip()))

    @staticmethod
    def _string_list(output, field: str) -> list[str]:
        values = getattr(output, field, ())
        return [str(value) for value in values or () if str(value).strip()]

    @staticmethod
    def _error_result(
        *,
        request: ToolExecutionRequest,
        started_at: datetime,
        started: float,
        status: ResultStatus,
        category: ErrorCategory,
        code: str,
        retryable: bool,
        error: Exception,
    ) -> ToolResult:
        message = str(error).strip() or code
        return ToolResult(
            tool_call_id=request.tool_call.action_id,
            tool_name=request.tool_call.tool_name,
            status=status,
            summary=f"{request.tool_call.tool_name} 执行失败。",
            error_category=category,
            error_code=code,
            error_message=message[:2_000],
            retryable=retryable,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            duration_ms=max(0, int((monotonic() - started) * 1000)),
        )


__all__ = ["ToolRuntime"]
