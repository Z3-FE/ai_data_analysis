"""切片 B 的真实结构化 PlanningAgent。"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.agent.daily_chat_agent import DailyChatAgent, is_greeting
from app.agent.planning_agent.contracts import (
    ActionIssuanceContext,
    PlannerActionDraft,
    PlannerModelClient,
    PlanningPort,
)
from app.agent.planning_agent.errors import PlannerFailure, planner_error
from app.agent.planning_agent.normalizer import normalize_action
from app.agent.planning_agent.validator import validate_draft
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state_result_store.contracts import ActionType, PlannerCapabilities, PlannerInput, NextAction

logger = logging.getLogger(__name__)


class PlanningAgent(PlanningPort):
    """调用统一 LLM，解析并校验一个待提交动作。"""

    def __init__(
        self,
        *,
        llm_client: PlannerModelClient,
        capabilities: PlannerCapabilities | None = None,
        daily_chat_agent: DailyChatAgent | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.capabilities = capabilities or PlannerCapabilities()
        self.daily_chat_agent = daily_chat_agent
        self.calls: list[PlannerInput] = []

    async def plan(
        self,
        value: PlannerInput,
        *,
        issuance: ActionIssuanceContext,
    ) -> NextAction:
        self.calls.append(value)
        prompt = load_prompt("plan_next_action").format(
            planner_input=json.dumps(value.model_dump(mode="json"), ensure_ascii=False)
        )
        raw: str | None = None
        try:
            # 仅首次规划的纯问候走快捷分支；确认回复与工具迭代仍交给 Planner。
            if (
                self.daily_chat_agent is not None
                and value.state_view.iteration == 0
                and issuance.action_seq == 1
                and is_greeting(value.state_view.original_goal)
            ):
                draft = PlannerActionDraft(action_type="daily_chat")
            else:
                raw = await self.llm_client.complete(prompt)
                draft = PlannerActionDraft.model_validate_json(self._extract_json(raw))
            if draft.action_type == "daily_chat":
                if self.daily_chat_agent is None or not self.capabilities.allow_final_answer:
                    raise planner_error("action_not_allowed", "当前 Planner 不允许日常聊天")
                answer = await self.daily_chat_agent.answer(messages=value.compiled_context.messages)
                draft = PlannerActionDraft(
                    action_type=ActionType.FINAL_ANSWER,
                    final_answer=answer,
                    rationale_summary="由日常聊天分支直接作答",
                )
            validate_draft(
                draft,
                tool_specs=value.tool_specs,
                capabilities=self.capabilities,
            )
            return normalize_action(draft, issuance)
        except Exception as exc:
            if isinstance(exc, PlannerFailure):
                raise
            # 原始输出是定位 invalid_output 的唯一线索，必须在源头留下。
            logger.warning(
                "Planner 输出无效，原始输出：%s",
                (raw or "<empty>")[:2000],
            )
            raise planner_error("invalid_output", f"Planner 输出无效: {exc}", retryable=True) from exc

    @staticmethod
    def _extract_json(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("未找到 JSON 对象")
        return text[start : end + 1]


class AutoLLMPlannerClient:
    """把项目 AutoLLMAdapter 接到 PlanningAgent 的最小适配器。"""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def complete(self, prompt: str) -> str:
        response = await self.llm_client.ainvoke_auto(prompt)
        return response.content


__all__ = ["AutoLLMPlannerClient", "PlanningAgent"]
