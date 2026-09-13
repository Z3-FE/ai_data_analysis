"""切片 B Planning Agent 的确定性动作校验。"""

from __future__ import annotations

from typing import Any

from app.agent.planning_agent.contracts import PlannerActionDraft
from app.agent.planning_agent.errors import planner_error
from app.agent.state_result_store.contracts import PlannerCapabilities, ToolSpec


def _matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    """覆盖当前工具契约需要的 JSON Schema 基础约束。"""
    expected = schema.get("type")
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def validate_draft(
    draft: PlannerActionDraft,
    *,
    tool_specs: tuple[ToolSpec, ...],
    capabilities: PlannerCapabilities,
) -> None:
    """校验能力开关、工具注册和工具参数，不产生状态副作用。"""
    if draft.action_type.value == "tool_call":
        if not capabilities.allow_tool_call:
            raise planner_error("action_not_allowed", "当前 Planner 不允许工具调用")
        assert draft.tool_call is not None
        spec = next((item for item in tool_specs if item.name == draft.tool_call.tool_name), None)
        if spec is None or not spec.enabled:
            raise planner_error("unknown_tool", f"工具未注册或未启用: {draft.tool_call.tool_name}")
        schema = spec.input_schema
        required = schema.get("required", [])
        missing = [name for name in required if name not in draft.tool_call.arguments]
        if missing:
            raise planner_error("invalid_tool_arguments", f"工具参数缺少字段: {missing}")
        properties = schema.get("properties", {})
        invalid = [
            name
            for name, value in draft.tool_call.arguments.items()
            if name in properties and not _matches_schema(value, properties[name])
        ]
        if invalid:
            raise planner_error("invalid_tool_arguments", f"工具参数类型错误: {invalid}")
    elif draft.action_type.value == "ask_user" and not capabilities.allow_ask_user:
        raise planner_error("action_not_allowed", "当前 Planner 不允许询问用户")
    elif draft.action_type.value == "final_answer" and not capabilities.allow_final_answer:
        raise planner_error("action_not_allowed", "当前 Planner 不允许直接回答")


__all__ = ["validate_draft"]
