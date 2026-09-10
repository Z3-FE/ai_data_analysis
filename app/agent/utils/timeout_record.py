"""统一记录 LLM 超时，便于后续定位和优化。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("app.agent.timeout")


def llm_identity(llm_client: Any) -> dict[str, str]:
    """从 AutoLLM 客户端读取非敏感的服务标识。"""
    config = getattr(llm_client, "adapter_config", None)
    return {
        "provider": str(getattr(config, "provider", "unknown")),
        "model": str(getattr(config, "model_name", "unknown")),
    }


def classify_timeout(error: BaseException) -> tuple[str, str]:
    """根据统一错误信息判断调用方式和超时类型。"""
    message = str(error)
    if "非流式" in message:
        return "invoke", "non_stream_idle"
    if (
        "流式响应空闲超时" in message
        or "流式请求超时" in message
        or "SQL 生成超时" in message
    ):
        return "stream", "stream_idle"
    return "unknown", "unknown"


def is_llm_timeout(error: BaseException) -> bool:
    """判断异常是否属于 LLM 等待超时，而不是 Python 沙箱超时。"""
    call_mode, timeout_kind = classify_timeout(error)
    return call_mode != "unknown" or timeout_kind != "unknown"


def record_llm_timeout(
    *,
    node: str,
    step: str = "",
    task_id: str = "",
    phase: str = "",
    call_mode: str,
    timeout_kind: str,
    timeout_seconds: float,
    error: BaseException | str,
    llm_client: Any = None,
    writer: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """写入结构化超时日志，并可选地发出 SSE 调试事件。"""
    identity = llm_identity(llm_client)
    message = str(error)
    record: dict[str, Any] = {
        "event": "llm_timeout",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "node": node,
        "step": step,
        "task_id": task_id,
        "phase": phase,
        "call_mode": call_mode,
        "timeout_kind": timeout_kind,
        "timeout_seconds": timeout_seconds,
        "provider": identity["provider"],
        "model": identity["model"],
        "error": message,
    }
    logger.warning("LLM 超时记录：%s", json.dumps(record, ensure_ascii=False, default=str))
    if writer is not None:
        writer({
            "type": "timeout",
            "step": step,
            "node": node,
            "status": "failed",
            **record,
        })
    return record
