"""Harness 工具注册表。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel

from app.agent.state_result_store.contracts import ToolSpec


class RegisteredTool(Protocol):
    name: str
    input_model: type[BaseModel]

    async def execute(self, value: Any) -> Any: ...


class ToolRegistry:
    """保存名称、规格和实现一致的已注册 Harness 工具。"""

    def __init__(self, tools: dict[str, tuple[ToolSpec, RegisteredTool]]) -> None:
        for name, (spec, tool) in tools.items():
            if name != spec.name or name != tool.name:
                raise ValueError(f"工具注册名称不一致: {name}")
            if not isinstance(tool.input_model, type) or not issubclass(
                tool.input_model, BaseModel
            ):
                raise ValueError(f"工具必须声明 Pydantic input_model: {name}")
        self._tools = dict(tools)

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
