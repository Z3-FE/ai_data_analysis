"""Harness 工具注册表。"""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.state_result_store.contracts import ToolSpec


class RegisteredTool(Protocol):
    async def execute(self, value: Any) -> Any: ...


class ToolRegistry:
    """切片 C 只注册 query_data，避免 API 层手工分派工具。"""

    def __init__(self, tools: dict[str, tuple[ToolSpec, RegisteredTool]]) -> None:
        self._tools = tools

    def get(self, name: str) -> tuple[ToolSpec, RegisteredTool]:
        try:
            spec, tool = self._tools[name]
        except KeyError as exc:
            raise ValueError(f"未注册工具: {name}") from exc
        if not spec.enabled:
            raise ValueError(f"工具未启用: {name}")
        return spec, tool

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec, _ in self._tools.values() if spec.enabled)


__all__ = ["RegisteredTool", "ToolRegistry"]
