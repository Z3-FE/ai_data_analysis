"""切片 B 的真实结构化 PlanningAgent。"""

from __future__ import annotations

import json
import logging
from typing import Any

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
from app.agent.state_result_store.contracts import PlannerCapabilities, PlannerInput, NextAction

logger = logging.getLogger(__name__)


class PlanningAgent(PlanningPort):
    """调用统一 LLM，解析并校验一个待提交动作。"""

    def __init__(
        self,
        *,
        llm_client: PlannerModelClient,
        capabilities: PlannerCapabilities | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.capabilities = capabilities or PlannerCapabilities()
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
            raw = await self.llm_client.complete(prompt)
            draft = PlannerActionDraft.model_validate_json(self._extract_json(raw))
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
