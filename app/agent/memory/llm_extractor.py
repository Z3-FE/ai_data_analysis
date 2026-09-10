"""使用结构化输出提取自动长期记忆候选。"""

import json
from typing import Any, Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from app.agent.memory.contracts import (
    MemoryCandidate,
    MemoryCandidateBatch,
    TurnMemoryInput,
)
from app.agent.memory.enums import MemoryType

ExtractionMode = Literal["automatic", "explicit"]


def _extract_json(text: str) -> str:
    """兼容 Markdown fenced JSON 和模型前后的少量说明文字。"""
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    start = value.find("{")
    end = value.rfind("}")
    return value[start : end + 1] if start >= 0 and end > start else value


class LlmMemoryExtractor:
    """让 LLM 提出候选，但不让它获得写库和覆盖权限。"""

    def __init__(self, llm_client: Any, *, version: str = "m3-v1") -> None:
        self.llm_client = llm_client
        self.version = version

    async def extract(
        self, turn: TurnMemoryInput, *, mode: ExtractionMode = "automatic"
    ) -> list[MemoryCandidate]:
        """按自动形成或显式请求模式提取结构化候选。"""
        parser = PydanticOutputParser(pydantic_object=MemoryCandidateBatch)
        prompt = PromptTemplate(
            template=(
                "你是长期记忆候选提取器。当前提取模式：{mode}。\n"
                "{mode_instructions}\n"
                "只可提取 semantic 或 episodic；working 由会话状态负责，"
                "perceptual 由附件流程负责。"
                "不得补充输入中不存在的信息，也不要提取密码、密钥、令牌或恢复码。\n"
                "每条候选必须脱离本轮对话仍可理解。可更新事实应生成稳定、"
                "语义化的 fact_key；具体历史任务应生成稳定 event_key，且不同"
                "事件不能共用同一个键。无法可靠归一化时留空。conditions 只"
                "保存事实成立的真实条件。Semantic 候选如包含明确实体关系，"
                "在 structured_data.entities 中输出 entity_id/name/entity_type，"
                "在 structured_data.relations 中输出 source_id/target_id/relation_type；"
                "没有明确关系时保持空数组，禁止推测。"
                "source_refs 必须为空，真实来源由后端绑定。\n"
                "用户问题：{input_text}\n"
                "助手最终回答：{assistant_content}\n"
                "执行模式：{execution_mode}\n"
                "最终输出类型：{output_type}\n"
                "受控最终输出：{output_payload}\n"
                "只能返回结构化候选，不能返回隐藏思考。\n{format_instructions}"
            ),
            input_variables=[
                "input_text",
                "assistant_content",
                "execution_mode",
                "output_type",
                "output_payload",
                "mode",
                "mode_instructions",
            ],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        values = {
            "input_text": turn.input_text,
            # 显式模式只允许结构化用户要求保存的原文，不能从助手回答派生事实。
            "assistant_content": (
                turn.assistant_content if mode == "automatic" else ""
            ),
            "execution_mode": turn.execution_mode,
            "output_type": turn.output_type,
            "output_payload": (
                self._compact_payload(turn.output_payload)
                if mode == "automatic"
                else "{}"
            ),
            "mode": mode,
            "mode_instructions": self._mode_instructions(mode),
        }
        rendered = prompt.format(**values)
        raw = await self._invoke(rendered)
        if not raw:
            return []
        candidates = parser.parse(_extract_json(raw)).candidates
        # 即使模型没有遵守提示词，也不能越过专用记忆类型边界。
        return [
            candidate
            for candidate in candidates
            if candidate.memory_type in {MemoryType.SEMANTIC, MemoryType.EPISODIC}
        ]

    @staticmethod
    def _mode_instructions(mode: ExtractionMode) -> str:
        """返回两种形成模式各自的提取边界。"""
        if mode == "explicit":
            return (
                "用户已明确要求记住。仅结构化用户要求保存的具体内容；"
                "若只是在引用附件则返回空数组，由 Perceptual Memory 处理。"
                "显式内容不因普通自动形成阈值而丢弃。"
            )
        return (
            "只提取高置信度、未来可复用的稳定事实或成功任务经验。"
            "普通寒暄、一次性数值、附件原文和未经证实的推测返回空数组。"
            "置信度是否达到保存要求由后端治理策略最终判断。"
        )

    @staticmethod
    def _compact_payload(payload: dict[str, Any]) -> str:
        """限制提示词中的结构化结果，避免复制大结果集。"""
        try:
            encoded = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            encoded = repr(payload)
        return encoded[:12000]

    async def _invoke(self, rendered_prompt: str) -> str:
        """兼容项目 AutoLLMAdapter 和普通 LangChain Runnable。"""
        ainvoke_auto = getattr(self.llm_client, "ainvoke_auto", None)
        if ainvoke_auto is not None:
            response = await ainvoke_auto(rendered_prompt)
            return str(getattr(response, "content", response) or "")
        response = await self.llm_client.ainvoke(rendered_prompt)
        if isinstance(response, str):
            return response
        content = getattr(response, "content", response)
        return str(content or "")


__all__ = ["LlmMemoryExtractor"]
