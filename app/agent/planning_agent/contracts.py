"""切片 B Planning Agent 的临时草稿和发行契约。"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from app.agent.context_engine.contracts import CompiledContext
from app.agent.state_result_store.contracts import (
    ActionType,
    ContractModel,
    NextAction,
    PlannerCapabilities,
    PlannerInput,
    PlannerStateView,
    ToolSpec,
)


class ToolCallDraft(ContractModel):
    """LLM 草稿中的工具调用；模型不能生成正式动作 ID。"""

    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0)


class AskUserDraft(ContractModel):
    """LLM 草稿中的用户询问。确认 ID 由后续确认模块生成。"""

    question: str = Field(min_length=1, max_length=2_000)
    reason_code: Literal[
        "missing_condition",
        "ambiguous_reference",
        "conflicting_definition",
        "tool_needs_user",
    ]
    required_fields: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_required_fields(self) -> "AskUserDraft":
        if any(not field.strip() for field in self.required_fields):
            raise ValueError("required_fields 不能包含空字段名")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields 不能重复")
        return self


class PlannerActionDraft(ContractModel):
    """LLM 解析结果；不进入状态、Checkpoint 或业务存储。"""

    action_type: ActionType
    tool_call: ToolCallDraft | None = None
    ask_user: AskUserDraft | None = None
    final_answer: str | None = Field(default=None, min_length=1, max_length=20_000)
    rationale_summary: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_payload(self) -> "PlannerActionDraft":
        payloads = (self.tool_call, self.ask_user, self.final_answer)
        if sum(value is not None for value in payloads) != 1:
            raise ValueError("PlannerActionDraft 必须且只能包含一个动作 payload")
        if self.action_type is ActionType.TOOL_CALL and self.tool_call is None:
            raise ValueError("tool_call 草稿必须包含 tool_call")
        if self.action_type is ActionType.ASK_USER and self.ask_user is None:
            raise ValueError("ask_user 草稿必须包含 ask_user")
        if self.action_type is ActionType.FINAL_ANSWER and not self.final_answer:
            raise ValueError("final_answer 草稿必须包含非空 final_answer")
        return self


class ActionIssuanceContext(ContractModel):
    """Loop Controller 提供给 Planner 的候选动作发行信息。"""

    run_id: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    action_seq: int = Field(ge=1)


class ActionIdFactory(Protocol):
    def issue(self, *, run_id: str, iteration: int, action_seq: int) -> str: ...


class ActionNormalizer(Protocol):
    def normalize(
        self,
        *,
        draft: PlannerActionDraft,
        issuance: ActionIssuanceContext,
    ) -> NextAction: ...


class PlannerModelClient(Protocol):
    async def complete(self, prompt: str) -> str: ...


class PlannerInputFactory(Protocol):
    def build(
        self,
        *,
        compiled_context: CompiledContext,
        state_view: PlannerStateView,
        tool_specs: tuple[ToolSpec, ...],
    ) -> PlannerInput: ...


class PlanningPort(Protocol):
    async def plan(
        self,
        value: PlannerInput,
        *,
        issuance: ActionIssuanceContext,
    ) -> NextAction: ...


__all__ = [
    "ActionIdFactory",
    "ActionIssuanceContext",
    "ActionNormalizer",
    "AskUserDraft",
    "PlannerActionDraft",
    "PlannerInputFactory",
    "PlannerModelClient",
    "PlanningPort",
    "ToolCallDraft",
]
