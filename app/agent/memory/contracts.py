"""记忆形成与治理使用的稳定领域协议。

这些模型只描述一轮对话如何产生长期记忆，不描述下一轮如何把记忆组装进
模型上下文。后者属于 ``app/agent/context_engine``。
"""

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.agent.memory.enums import (
    MemoryDecisionAction,
    MemoryFormationStatus,
    MemoryFormationTrigger,
    MemoryScope,
    MemoryType,
)
from app.agent.memory.interfaces import MemoryCreate


class TurnMemoryInput(BaseModel):
    """一轮 Agent 完成后交给记忆形成模块的受控输入。"""

    # 轮次所属用户；所有记忆和审计查询都以此字段隔离。
    user_id: str = Field(min_length=1, max_length=128)
    # 业务会话 ID，同时也是 LangGraph thread_id。
    conversation_id: str = Field(min_length=1, max_length=128)
    # 当前用户问题对应的稳定轮次 ID。
    turn_id: str = Field(min_length=1, max_length=128)
    # 当前轮次的 Agent 执行尝试 ID。
    run_id: str = Field(min_length=1, max_length=128)
    # 用户本轮原始输入；只在形成过程中使用，不复制到审计表。
    input_text: str = ""
    # 助手最终可见回答；不包含隐藏思考和执行事件。
    assistant_content: str = ""
    # 路由后的执行模式，例如 daily_chat、single_query 或 analysis。
    execution_mode: str = ""
    # 本轮最终状态，例如 completed、partial 或 failed。
    status: str = ""
    # 本轮最终结构化输出类型，例如 query_result 或 rendered_report。
    output_type: str = ""
    # 受控最终输出；禁止传入完整 AgentState、隐藏思考或完整执行事件。
    output_payload: dict[str, Any] = Field(default_factory=dict)
    # 项目级上下文的项目 ID；当前没有项目时为空。
    project_id: str | None = None
    # 本轮明确关联的附件 ID；当前只形成已经完成文本提取的附件记忆。
    asset_ids: list[str] = Field(default_factory=list)

    @field_validator("user_id", "conversation_id", "turn_id", "run_id")
    @classmethod
    def strip_identity(cls, value: str) -> str:
        """清理身份字段，避免仅由空白字符组成。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("轮次身份字段不能为空")
        return cleaned


class MemoryCandidate(BaseModel):
    """规则或 LLM 提出的长期记忆候选，尚未获得写库权限。"""

    # 候选内部 ID，用于审计串联，不作为最终 memory_id。
    candidate_id: str = Field(default_factory=lambda: str(uuid4()))
    # 候选类型；Working Memory 不能由形成流程写入。
    memory_type: MemoryType
    # 可以脱离当前对话单独理解的候选正文。
    content: str = Field(min_length=1, max_length=4000)
    # 建议的可见范围；后端仍会根据真实身份校验。
    scope: MemoryScope = MemoryScope.USER
    # 可更新事实的稳定键，例如 user.profile.name。
    fact_key: str = Field(default="", max_length=255)
    # 情景事件的稳定键；不同事件必须使用不同键，避免历史经验互相覆盖。
    event_key: str = Field(default="", max_length=255)
    # 事实的结构化值；没有稳定值时可以为空。
    value: Any = None
    # 事实成立的条件，不同条件下的事实可以同时保留。
    conditions: dict[str, Any] = Field(default_factory=dict)
    # 类型相关扩展数据；实体关系只允许 Semantic Memory 使用。
    structured_data: dict[str, Any] = Field(default_factory=dict)
    # 候选长期价值，范围 0 到 1。
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    # 提取器对内容准确性的判断，范围 0 到 1。
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # 为什么认为该内容值得形成长期记忆。
    reason: str = Field(default="", max_length=1000)
    # 候选声称的来源只用于审计；形成服务会用本轮真实来源覆盖它。
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=8)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        """候选正文不能只包含空白字符。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("记忆候选正文不能为空")
        return cleaned


class MemoryCandidateBatch(BaseModel):
    """LLM 一次结构化提取返回的候选集合。"""

    # 没有长期价值时必须返回空数组，禁止为了满足格式机械生成记忆。
    candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=8)


class FormationEligibility(BaseModel):
    """确定性预筛选对本轮是否进入形成流程的判断。"""

    # 是否需要运行记忆提取器。
    eligible: bool
    # 显式请求、自动候选或跳过。
    trigger: MemoryFormationTrigger
    # 预筛选原因，写入审计表但不进入模型上下文。
    reason: str


class GovernedCandidate(BaseModel):
    """已经通过后端治理、可以进入去重和写入流程的候选。"""

    # 原始候选，用于保留提取原因和候选 ID。
    candidate: MemoryCandidate
    # 后端根据可信身份、来源和策略构造的最终写入请求。
    request: MemoryCreate

    model_config = {"arbitrary_types_allowed": True}


class MemoryDecision(BaseModel):
    """一条候选经过治理、去重和写入后的可审计决定。"""

    # 对应 MemoryCandidate.candidate_id。
    candidate_id: str
    # 候选最终类型；预筛选直接跳过时可以为空。
    memory_type: MemoryType | None = None
    # created、replaced、duplicate、rejected 或 failed。
    action: MemoryDecisionAction
    # 后端做出该决定的可解释原因。
    reason: str
    # 创建、命中或替换后得到的记忆 ID。
    memory_id: str | None = None
    # 创建新版本时被替换的旧记忆 ID。
    replaced_memory_id: str | None = None
    # 可更新事实键；审计表不重复保存候选全文。
    fact_key: str = ""
    # 实际采用的类型化身份键：Semantic fact_key、Episodic event_key 或附件 ID。
    identity_key: str = ""
    # 候选正文 SHA-256，用于审计去重且不泄露被拒绝内容。
    content_hash: str = ""


class MemoryFormationResult(BaseModel):
    """一次形成任务提供给服务层的紧凑结果。"""

    formation_run_id: str
    status: MemoryFormationStatus
    trigger: MemoryFormationTrigger
    candidate_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    duplicate_count: int = 0
    replaced_count: int = 0
    # 去重、治理或投影同步中的单候选失败数量。
    failed_count: int = 0
    decisions: list[MemoryDecision] = Field(default_factory=list)
    error_message: str = ""
