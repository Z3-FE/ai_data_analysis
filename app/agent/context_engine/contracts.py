"""ContextEngine 使用的稳定领域协议。

这些结构描述一次上下文构建，不暴露 PostgreSQL、Qdrant、Neo4j 或 LangGraph
的具体对象。Agent 只消费 ``CompiledContext``。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.context_engine.harness_context_contracts import RuntimeContext

from app.agent.memory.enums import MemoryType


class ContextSourceKind(StrEnum):
    """进入 ContextEngine 的候选信息来源。"""

    WORKING = "working"
    SUMMARY = "conversation_summary"
    SEMANTIC = "semantic"
    EPISODIC = "episodic"
    PERCEPTUAL = "perceptual"
    RAG = "rag"


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """一个 Agent 的可注入上下文构建策略。"""

    # 编译后全部输入消息允许占用的最大 token 数。
    max_context_tokens: int = 12_000
    # 为分区标题、引用标签和消息协议预留的 token。
    format_reserve_tokens: int = 384
    # Working Memory 可以使用的目标预算比例；超出时形成增量摘要。
    working_history_ratio: float = 0.5
    # Working 预算中优先留给原始近期消息的比例。
    recent_history_ratio: float = 0.65
    # 较早会话摘要的最大 token 数。
    summary_max_tokens: int = 1_200
    # 单次交给摘要器的新增历史 token 上限；长历史按该值分批增量合并。
    summary_batch_tokens: int = 6_000
    # 单个候选在本轮上下文中的最大 token 数。
    max_item_tokens: int = 2_000
    # 候选剩余空间低于该值时不再做无意义的局部截断。
    min_compression_tokens: int = 64
    # 每种长期记忆初始召回上限；最终仍由 token 预算筛选。
    memory_recall_limit: int = 8
    # 综合分低于该值的非必要候选不进入上下文。
    min_selection_score: float = 0.18
    # 各项评分权重；调用方可以按 Agent 类型替换整套 Policy。
    relevance_weight: float = 0.45
    importance_weight: float = 0.15
    confidence_weight: float = 0.1
    recency_weight: float = 0.15
    priority_weight: float = 0.15

    def __post_init__(self) -> None:
        if self.max_context_tokens <= 0:
            raise ValueError("max_context_tokens 必须大于 0")
        if self.format_reserve_tokens < 0:
            raise ValueError("format_reserve_tokens 不能小于 0")
        if self.format_reserve_tokens >= self.max_context_tokens:
            raise ValueError("format_reserve_tokens 必须小于 max_context_tokens")
        for name in (
            "summary_max_tokens",
            "summary_batch_tokens",
            "max_item_tokens",
            "min_compression_tokens",
            "memory_recall_limit",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        if not 0 <= self.min_selection_score <= 1:
            raise ValueError("min_selection_score 必须在 0 到 1 之间")
        for name in ("working_history_ratio", "recent_history_ratio"):
            value = getattr(self, name)
            if not 0 < value <= 1:
                raise ValueError(f"{name} 必须在 0 到 1 之间")
        weights = (
            self.relevance_weight,
            self.importance_weight,
            self.confidence_weight,
            self.recency_weight,
            self.priority_weight,
        )
        if any(value < 0 for value in weights) or sum(weights) <= 0:
            raise ValueError("上下文选择权重必须为非负数且总和大于 0")


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """一次上下文构建请求。"""

    # 当前请求所属用户，所有记忆读取必须使用该身份隔离。
    user_id: str
    # 当前业务会话 ID，同时对应 LangGraph thread_id。
    conversation_id: str
    # 用户本轮原始问题。
    query: str
    # 当前 Agent 的角色、行为约束和工具使用规则。
    system_instructions: str
    # 策略选择使用的 Agent 类型，例如 data_agent 或 decision_agent。
    agent_type: str = "general"
    # 项目级记忆和知识检索使用的项目 ID。
    project_id: str | None = None
    # 本轮直接上传或明确选择的附件 ID。
    asset_ids: tuple[str, ...] = ()
    # 显式指定长期记忆类型；None 表示交给 Planner，空元组表示不召回。
    memory_types: tuple[MemoryType, ...] | None = None
    # 是否允许当前 Agent 使用外部知识/RAG。
    enable_rag: bool = False
    # 覆盖 ContextPolicy 的本轮预算；为空时使用策略默认值。
    token_budget: int | None = None
    # Harness 运行态的受控只读投影；None 保持旧消息序列。
    runtime_context: "RuntimeContext | None" = None


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    """对会话和附件指代的确定性解析结果。"""

    # 本轮最终需要读取的附件 ID，包含当前附件和已解析历史附件。
    asset_ids: tuple[str, ...] = ()
    # 其中来自历史消息的附件 ID。
    historical_asset_ids: tuple[str, ...] = ()
    # 当前问题是否明显依赖历史会话。
    needs_working_context: bool = False
    # 无法解析的指代说明；ContextEngine 不会猜测未知对象。
    unresolved_references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextRetrievalPlan:
    """本轮需要从哪些来源召回候选。"""

    # Working Memory 始终可以读取，但 Planner 可以决定最终是否强调历史。
    include_working: bool = True
    # 需要检索的长期记忆类型组合。
    memory_types: tuple[MemoryType, ...] = ()
    # 是否调用已经配置的业务知识检索器。
    use_rag: bool = False
    # 检索使用的问题；允许 Planner 消解简单指代，但不得编造业务条件。
    search_query: str = ""
    # 可审计的规划原因。
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ContextSourceRef:
    """候选信息可回溯的来源引用。"""

    # 来源对象类型，例如 memory、turn、asset 或 document。
    source_type: str
    # 来源对象 ID。
    source_id: str
    # 来源对象内部的可选字段路径或片段标识。
    source_path: str | None = None


@dataclass(frozen=True, slots=True)
class ContextItem:
    """Gather 阶段产生的统一上下文候选。"""

    # 候选在本轮构建中的稳定 ID。
    item_id: str
    # 候选来源类别。
    source_kind: ContextSourceKind
    # 可以直接进入模型上下文的正文。
    content: str
    # Working Message 的消息角色；其他来源为空。
    role: str | None = None
    # 使用当前 TokenCounter 计算出的正文 token 数。
    token_count: int = 0
    # 与本轮问题的相关性，范围 0 到 1。
    relevance: float = 0.0
    # 信息的长期重要性，范围 0 到 1。
    importance: float = 0.5
    # 信息来源或提取结果的可信度，范围 0 到 1。
    confidence: float = 1.0
    # 时间近因性，范围 0 到 1。
    recency: float = 0.5
    # 产品或 Agent 策略优先级，范围 0 到 100。
    priority: int = 50
    # 必须优先保留的信息，例如用户明确引用的附件。
    required: bool = False
    # 类型相关的结构化信息，不直接全部渲染给模型。
    structured_data: dict[str, Any] = field(default_factory=dict)
    # 真实来源引用。
    source_refs: tuple[ContextSourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextKnowledgeItem:
    """业务 RAG 检索器返回的统一文档片段。"""

    # 文档或片段稳定 ID。
    item_id: str
    # 可供模型引用的正文。
    content: str
    # 检索相关性，范围 0 到 1。
    relevance: float
    # 原始文档标题。
    title: str = ""
    # 文档 URI、知识库 ID 等受控元数据。
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextConversationSummary:
    """较早 Working Memory 的可持续增量摘要。"""

    # 摘要稳定 ID。
    summary_id: str
    # 摘要所属用户。
    user_id: str
    # 摘要所属会话。
    conversation_id: str
    # 摘要正文。
    content: str
    # 摘要覆盖的首条 Working Message 序号。
    covered_from_index: int
    # 摘要覆盖的末条 Working Message 序号。
    covered_through_index: int
    # 当前摘要累计覆盖的消息数量。
    source_message_count: int
    # 摘要正文 token 数。
    token_count: int
    # 每次增量更新递增，用于追踪摘要演化。
    version: int = 1
    # 摘要创建时间。
    created_at: datetime | None = None
    # 摘要最近更新时间。
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ContextSelectionDecision:
    """一个候选在本轮选择和压缩阶段的处理结果。"""

    # 被处理候选的稳定 ID。
    item_id: str
    # 候选来自 Working、长期记忆、附件或 RAG。
    source_kind: ContextSourceKind
    # 候选最终是否进入模型上下文。
    selected: bool
    # 选择、过滤、去重或超预算的固定原因。
    reason: str
    # ContextEngine 跨来源选择分数，范围 0 到 1。
    score: float
    # 进入压缩前的正文 token 数。
    original_tokens: int
    # 最终进入上下文的 token 数；未选中时为 0。
    final_tokens: int
    # 是否只针对本轮执行过候选压缩。
    compressed: bool = False
    # 只记录来源身份，不记录候选正文。
    source_refs: tuple[ContextSourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextSections:
    """编译前后均可查看的上下文语义分区。"""

    # 被选中的较早会话摘要。
    conversation_summary: tuple[str, ...] = ()
    # 被选中的 Semantic Memory 事实、偏好和规则。
    known_facts: tuple[str, ...] = ()
    # 被选中的 Episodic Memory 历史任务与经验。
    prior_work: tuple[str, ...] = ()
    # 被选中的 Perceptual Memory 或直接附件提取内容。
    attachments: tuple[str, ...] = ()
    # 可选业务 RAG 返回的外部证据。
    external_evidence: tuple[str, ...] = ()
    # 无法安全解析的历史或附件指代，供模型主动澄清而不是猜测。
    unresolved_references: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextBuildTrace:
    """一次 ContextEngine 构建的可审计摘要，不保存候选正文。"""

    # 单次上下文构建 ID。
    build_id: str
    # 构建所属用户 ID。
    user_id: str
    # 构建所属会话 ID。
    conversation_id: str
    # 消费上下文的 Agent 类型。
    agent_type: str
    # 用户原问题的 SHA-256；trace 不复制原问题。
    query_hash: str
    # completed 或 failed；pending 状态只存在数据库行中。
    status: str
    # 本轮上下文总 token 预算。
    token_budget: int
    # 最终标准消息的真实 token 数。
    final_token_count: int
    # Gather 阶段候选总数。
    candidate_count: int
    # 最终进入模型上下文的候选数量。
    selected_count: int
    # 去除 search_query 和自由文本原因后的紧凑召回计划。
    retrieval_plan: ContextRetrievalPlan
    # 解析到的历史依赖、附件 ID 和未解析引用。
    reference_resolution: ReferenceResolution
    # 候选级决定，不包含候选正文。
    decisions: tuple[ContextSelectionDecision, ...] = ()
    # 本轮是否扩展了持久化会话摘要。
    summary_updated: bool = False
    # 构建错误的受控摘要；成功时为空。
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class CompiledContext:
    """ContextEngine 输出给 Agent 的唯一结果。"""

    # 本次上下文构建 ID。
    build_id: str
    # 可直接传给 LangChain ChatModel 的 role/content 消息。
    messages: tuple[dict[str, Any], ...]
    # 便于调试和不同 Agent 二次消费的结构化分区。
    sections: ContextSections
    # 最终消息的实际 token 数。
    token_count: int
    # 本轮解析到的附件 ID。
    resolved_asset_ids: tuple[str, ...]
    # 完整选择 trace；不含候选正文。
    trace: ContextBuildTrace


__all__ = [
    "CompiledContext",
    "ContextBuildTrace",
    "ContextConversationSummary",
    "ContextItem",
    "ContextKnowledgeItem",
    "ContextPolicy",
    "ContextRequest",
    "ContextRetrievalPlan",
    "ContextSections",
    "ContextSelectionDecision",
    "ContextSourceKind",
    "ContextSourceRef",
    "ReferenceResolution",
]
