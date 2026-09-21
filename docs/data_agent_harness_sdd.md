# Data Agent Harness 技术设计文档（SDD）

## 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档名称 | Data Agent Harness 技术设计文档 |
| 架构依据 | data-agent-harness-final.mmd |
| 适用范围 | Data Agent Harness 的运行调度、上下文、记忆、工具、状态持久化和可观测性 |
| 目标语言 | Python 3.14 |
| 当前运行框架 | LangGraph |
| 当前事实数据库 | PostgreSQL |
| 当前向量投影 | Qdrant |
| 当前关系投影 | Neo4j，仅 Semantic Memory |

本文是研发团队的编码基线。流程图中的节点是模块边界；本文的数据结构、接口、状态转移和错误策略是实现约束。当前仓库已经具备 Memory、ContextEngine、现有 LangGraph Agent 图和 PostgreSQL Checkpointer 基础，但 Loop Controller、Planning Agent 和 Tool Runtime 尚未全部接入现有执行链。本文描述目标实现，不表示所有代码已经完成。

## 1. 核心数据模型定义（DTO / Entity）

实现可以使用 dataclass、Pydantic 或 TypedDict，但字段类型、必填性、枚举值和业务含义必须保持一致。以下示例采用 Python 类型表达，字段注释使用 #。

### 1.1 枚举定义

    from enum import StrEnum

    class RunStatus(StrEnum):
        # 一次 Harness Run 的生命周期状态。
        RUNNING = "running"
        WAITING_CONFIRMATION = "waiting_confirmation"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"
        TIMEOUT = "timeout"

    class LoopPhaseStatusType(StrEnum):
        # Loop Controller 当前所在的控制阶段。
        START_RUN = "start_run"
        RESTORE_RUN = "restore_run"
        BUILD_CONTEXT = "build_context"
        PLAN = "plan"
        VALIDATE_ACTION = "validate_action"
        EXECUTE_TOOL = "execute_tool"
        HANDLE_RESULT = "handle_result"
        RECORD_OBSERVATION = "record_observation"
        WAIT_CONFIRMATION = "wait_confirmation"
        FINALIZATION = "finalization"

    class ActionType(StrEnum):
        # Planning Agent 允许提交给 Loop Controller 的动作类型。
        TOOL_CALL = "tool_call"
        ASK_USER = "ask_user"
        FINAL_ANSWER = "final_answer"

    class ResultStatus(StrEnum):
        # Tool Runtime 归一化后的结果状态。
        SUCCESS = "success"
        PARTIAL = "partial"
        TEMPORARY_ERROR = "temporary_error"
        NEEDS_USER = "needs_user"
        UNRECOVERABLE_ERROR = "unrecoverable_error"

    class ConfirmationStatus(StrEnum):
        # 当前运行的用户确认状态。
        NOT_REQUIRED = "not_required"
        PENDING = "pending"
        CONFIRMED = "confirmed"
        REJECTED = "rejected"

    class ToolCallStatus(StrEnum):
        # 一次具体工具调用的生命周期状态。
        PENDING = "pending"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        SKIPPED = "skipped"

    class MemoryType(StrEnum):
        # Memory 的四种业务类型；Working 不写入长期记忆事实表。
        WORKING = "working"
        SEMANTIC = "semantic"
        EPISODIC = "episodic"
        PERCEPTUAL = "perceptual"

    class MemoryScope(StrEnum):
        # 长期记忆的可见作用域。
        USER = "user"
        CONVERSATION = "conversation"
        PROJECT = "project"

    class MemoryStatus(StrEnum):
        # 长期记忆事实的生命周期状态。
        ACTIVE = "active"
        SUPERSEDED = "superseded"
        FORGOTTEN = "forgotten"
        EXPIRED = "expired"

枚举使用约束：

- ResultStatus.NEEDS_USER 表示需要用户补充或确认，不属于普通失败重试。
- TEMPORARY_ERROR 只有在动作可安全重试时才允许进入 RetryTool。
- Planning Agent 不能返回未定义的 ActionType；非法值直接进入动作校验失败路径。
- Working Memory 只由 AgentState.messages 和 LangGraph Checkpointer 管理，不写入 agent_memories。

### 1.2 HarnessRunState

HarnessRunState 是 Loop Controller 管理的一次完整运行现场，也是 LangGraph Checkpointer 保存和恢复的核心状态。它必须能够支持工具结果回来后继续规划、等待用户确认后恢复，以及失败后安全结束。

    from dataclasses import dataclass, field
    from datetime import datetime
    from typing import Any

    @dataclass
    class HarnessRunState:
        # 必填：用户隔离键；Memory 和工具权限检查必须使用它。
        user_id: str
        # 必填：业务会话 ID。
        conversation_id: str
        # 必填：LangGraph Checkpointer 使用的线程 ID；当前与 conversation_id 对应。
        thread_id: str
        # 必填：当前请求或用户恢复确认的文本。
        input_text: str
        # 必填：当前用户轮次 ID。
        turn_id: str
        # 必填：一次 Harness Run 的稳定 ID；恢复时保持不变。
        run_id: str
        # 必填：运行生命周期状态。
        status: RunStatus
        # 必填：Loop Controller 当前阶段。
        phase: LoopPhaseStatusType

        # 必填：当前内部循环次数；本文约定从 1 开始。
        iteration: int
        # 必填：Planning Agent 失败和非法动作重试次数。
        planner_retry_count: int
        # 必填：同一 action_id 的工具重试次数；产生新动作后归零。
        tool_retry_count: int
        # 必填：ContextEngine 临时失败重试次数。
        context_retry_count: int
        # 必填：本次运行允许的最大内部循环次数。
        max_iterations: int
        # 必填：本次运行的绝对截止时间。
        deadline_at: datetime

        # 必填：用户最初的目标；恢复确认时不可覆盖。
        original_goal: str
        # 必填：已确认的指标口径、时间范围和其他任务条件。
        resolved_conditions: dict[str, Any]
        # 必填：最近一次 Planning Agent 动作；尚未规划时为 None。
        next_action: NextAction | None
        # 必填：已发起工具调用的受控摘要。
        tool_calls: list[dict[str, Any]] = field(default_factory=list)
        # 必填：按完成顺序保存的 ToolResult。
        tool_results: list[ToolResult] = field(default_factory=list)
        # 必填：给下一轮 Planning Agent 的观察摘要。
        observations: list[dict[str, Any]] = field(default_factory=list)

        # 必填：当前用户确认状态。
        confirmation_status: ConfirmationStatus = ConfirmationStatus.NOT_REQUIRED
        # 可选：等待用户回答的问题。
        confirmation_question: str | None = None
        # 可选：等待确认的原因或冲突说明。
        confirmation_reason: str | None = None
        # 可选：用户确认后的结构化条件。
        confirmation_payload: dict[str, Any] | None = None

        # 可选：当前请求或历史引用关联的附件 ID。
        asset_ids: list[str] = field(default_factory=list)
        # 可选：最近一次 ContextEngine 构建 ID。
        context_build_id: str | None = None
        # 可选：上下文来源 ID 和选择原因，不保存完整 prompt。
        context_refs: list[dict[str, Any]] = field(default_factory=list)
        # 可选：Planning Agent 最终生成的答案正文。
        answer_content: str | None = None
        # 可选：最终报告产物引用。
        report_ref: str | None = None
        # 必填：脱敏后的运行错误列表。
        errors: list[dict[str, Any]] = field(default_factory=list)
        # 必填：创建时间。
        created_at: datetime = field(default_factory=datetime.utcnow)
        # 必填：最近一次状态更新时间。
        updated_at: datetime = field(default_factory=datetime.utcnow)

字段必填性与实现规则：

| 字段 | 类型 | 必填 | 业务含义 |
| --- | --- | --- | --- |
| user_id | str | 是 | 用户隔离和权限判断的唯一身份键 |
| conversation_id | str | 是 | 业务会话键 |
| thread_id | str | 是 | LangGraph Checkpointer 线程键 |
| input_text | str | 是 | 当前轮输入或恢复时的用户确认文本 |
| turn_id | str | 是 | 当前用户轮次键 |
| run_id | str | 是 | 本次运行键，重试和恢复时保持关联 |
| status | RunStatus | 是 | 当前运行是否继续、等待、完成或失败 |
| phase | LoopPhaseStatusType | 是 | Loop Controller 当前节点阶段 |
| iteration | int | 是 | 内部循环次数 |
| planner_retry_count | int | 是 | Planner 失败或动作非法的累计重试次数 |
| tool_retry_count | int | 是 | 当前 action_id 的工具重试次数 |
| context_retry_count | int | 是 | ContextEngine 临时失败次数 |
| max_iterations | int | 是 | 防止模型或工具无限循环 |
| deadline_at | datetime | 是 | 运行级超时边界 |
| original_goal | str | 是 | 整个 Run 的原始目标 |
| resolved_conditions | dict[str, Any] | 是 | 已确认的业务条件和用户选择 |
| next_action | NextAction 或 None | 是 | 最近一次规划动作；初始值为 None |
| tool_calls | list[dict] | 是 | 工具调用摘要、action_id、状态和耗时 |
| tool_results | list[ToolResult] | 是 | 已返回的工具结果 |
| observations | list[dict] | 是 | 供后续规划使用的受控观察摘要 |
| confirmation_status | ConfirmationStatus | 是 | 当前是否等待用户确认 |
| confirmation_question | str 或 None | 否 | 返回前端的确认问题 |
| confirmation_reason | str 或 None | 否 | 为什么必须等待用户 |
| confirmation_payload | dict 或 None | 否 | 用户确认后的结构化结果 |
| asset_ids | list[str] | 否 | 当前运行关联的附件身份 |
| context_build_id | str 或 None | 否 | 最近一次 CompiledContext 的构建 ID |
| context_refs | list[dict] | 否 | 上下文来源和选择追踪 |
| answer_content | str 或 None | 否 | 最终回答正文 |
| report_ref | str 或 None | 否 | 前端可重新读取的报告引用 |
| errors | list[dict] | 是 | 脱敏错误码、错误分类和说明 |
| created_at | datetime | 是 | Run 创建时间 |
| updated_at | datetime | 是 | 最后一次状态更新时间 |

实现约束：

- tool_results、observations 和 tool_calls 只能保存有限大小的摘要、引用和状态；完整结果通过 result_ref 读取。
- errors 只能保存受控错误码和脱敏说明，禁止保存 API Key、密码、Token、私钥和完整堆栈。
- 成功生成合法 NextAction 后，planner_retry_count 清零；同一 action_id 重试时不清零 tool_retry_count。
- Planning Agent 生成新 action_id 后，tool_retry_count 清零。
- 用户恢复确认时，original_goal、run_id、conversation_id 和已经完成的工具结果不能被覆盖。

### 1.3 NextAction

NextAction 是 Planning Agent 输出给 PlannerGate 的动作 DTO。Planning Agent 只提出动作，不能直接访问 PostgreSQL、Qdrant、Neo4j 或执行具体业务逻辑。

    @dataclass
    class NextAction:
        # 必填：动作类别。
        action_type: ActionType
        # 必填：动作稳定 ID，用于审计、幂等和结果关联。
        action_id: str
        # tool_call 必填；其他动作必须为 None。
        tool_name: str | None
        # tool_call 的结构化参数；其他动作必须为空字典。
        arguments: dict[str, Any]
        # ask_user 必填：返回给用户的问题或确认请求。
        user_message: str | None
        # final_answer 必填：最终答案正文或答案载荷。
        answer_content: str | None
        # final_answer 可选：最终报告引用。
        report_ref: str | None
        # 必填：面向审计的简短原因，不是隐藏思维链。
        rationale: str
        # 可选：期望的 ToolResult 类型。
        expected_output: str | None
        # 必填：Planning Agent 使用的提示词和 Schema 版本。
        planner_version: str

| action_type | 必填字段 | 校验规则 |
| --- | --- | --- |
| tool_call | tool_name、arguments | tool_name 必须已注册，arguments 必须通过 ToolSpec Schema |
| ask_user | user_message | user_message 非空；不允许同时携带未确认的 tool_call |
| final_answer | answer_content 或 report_ref | 至少有一个非空；必须与当前证据状态一致 |

### 1.4 ToolResult

ToolResult 是 Tool Runtime 返回给 Loop Controller 的统一观察结果。它必须能够关联到一个 NextAction，且不能把任意大小的工具原始输出直接写入 HarnessRunState。

    @dataclass
    class ToolResult:
        # 必填：对应 NextAction.action_id。
        action_id: str
        # 必填：工具注册名称。
        tool_name: str
        # 必填：本次执行尝试的唯一 ID。
        tool_call_id: str
        # 必填：归一化后的结果状态。
        status: ResultStatus
        # 必填：有限长度的可读结果摘要。
        summary: str
        # 可选：完整查询结果、分析结果或报告的稳定引用。
        result_ref: str | None
        # 必填：真实数据、文档或报告来源引用。
        evidence_refs: list[dict[str, Any]]
        # 必填：结果限制、缺失条件和降级说明。
        limitations: list[str]
        # 可选：受控错误码；成功时为空。
        error_code: str | None
        # 可选：面向用户的脱敏错误信息。
        error_message: str | None
        # 必填：工具执行耗时，单位毫秒。
        duration_ms: int
        # 必填：当前 action_id 的执行尝试次数，首次为 1。
        attempt: int
        # 必填：是否可以安全重试该动作。
        retryable: bool
        # 必填：工具开始时间。
        started_at: datetime
        # 必填：工具完成时间。
        finished_at: datetime

| 字段 | 类型 | 必填 | 业务含义 |
| --- | --- | --- | --- |
| action_id | str | 是 | 将结果关联回 Planning Agent 的动作 |
| tool_name | str | 是 | 实际注册工具名 |
| tool_call_id | str | 是 | 一次具体执行尝试的唯一 ID |
| status | ResultStatus | 是 | 成功、部分成功、临时错误、需用户或不可恢复错误 |
| summary | str | 是 | 给下一轮 ContextEngine 使用的有限摘要 |
| result_ref | str 或 None | 否 | 完整结果的外部稳定引用 |
| evidence_refs | list[dict] | 是 | 真实结果、指标、报告或文档的引用 |
| limitations | list[str] | 是 | 缺失字段、数据范围和计算限制 |
| error_code | str 或 None | 否 | 受控错误编码 |
| error_message | str 或 None | 否 | 可安全展示的错误说明 |
| duration_ms | int | 是 | 执行耗时 |
| attempt | int | 是 | 同一动作的第几次尝试 |
| retryable | bool | 是 | Tool Runtime 对该动作是否允许重试 |
| started_at | datetime | 是 | 执行开始时间 |
| finished_at | datetime | 是 | 执行完成时间 |

### 1.5 CompiledContext

CompiledContext 是 ContextEngine 每次构建的输出，也是 Planning Agent 的输入。它只表示一次模型调用实际可见的上下文，不是长期记忆实体。

    @dataclass
    class CompiledContext:
        # 必填：一次上下文构建的唯一 ID。
        build_id: str
        # 必填：发送给 Planning Agent 的标准聊天消息。
        messages: list[dict[str, str]]
        # 必填：按语义组织的上下文分区。
        sections: dict[str, list[str]]
        # 必填：编译后消息实际使用的 token 数。
        token_count: int
        # 必填：本次构建允许的最大 token 数。
        token_budget: int
        # 必填：当前请求解析出的附件 ID。
        resolved_asset_ids: list[str]
        # 必填：候选来源、选择、去重、摘要和压缩的紧凑追踪。
        trace: dict[str, Any]

建议的 sections 键：

| 键 | 类型 | 业务含义 |
| --- | --- | --- |
| conversation_summary | list[str] | 较早会话的持久化摘要 |
| working_messages | list[str] | 当前会话保留的近期消息 |
| known_facts | list[str] | Semantic Memory 选中的事实、偏好和规则 |
| prior_work | list[str] | Episodic Memory 选中的历史任务经验 |
| referenced_attachments | list[str] | Perceptual Memory 解析出的附件内容摘要 |
| tool_observations | list[str] | 当前 Run 已完成工具结果的摘要和引用 |
| external_evidence | list[str] | 业务知识或 Meta RAG 的证据片段 |
| unresolved_references | list[str] | 无法安全解析的历史或附件引用 |

CompiledContext 的实现规则：

- messages 必须包含系统规则和当前问题；历史、记忆、工具结果以受控辅助内容注入。
- token_count 必须由当前模型对应的 TokenCounter 计算，不能使用字符数代替。
- trace 只保存来源 ID、分数、token 数和处理原因，不复制完整候选正文。
- 完整 prompt 不写入长期记忆，也不作为下一轮的唯一历史来源。

### 1.6 配套 DTO

    @dataclass
    class ContextRequest:
        # 必填：用户隔离键。
        user_id: str
        # 必填：当前业务会话。
        conversation_id: str
        # 必填：本轮问题或用户恢复确认内容。
        query: str
        # 必填：Data Agent 系统规则和工具约束。
        system_instructions: str
        # 可选：消费上下文的 Agent 类型。
        agent_type: str = "data_agent"
        # 可选：项目作用域。
        project_id: str | None = None
        # 可选：本轮直接上传或选择的附件 ID。
        asset_ids: list[str] = field(default_factory=list)
        # 可选：指定记忆类型；为空时由上下文策略决定。
        memory_types: list[MemoryType] | None = None
        # 可选：是否允许业务知识或 Meta RAG。
        enable_rag: bool = False
        # 可选：本轮上下文 token 上限。
        token_budget: int | None = None

    @dataclass
    class PlannerStateView:
        # 必填：当前任务目标。
        goal: str
        # 必填：当前计划和已完成项的受控摘要。
        plan_summary: str
        # 必填：ToolResult 的摘要、引用和状态。
        observations: list[dict[str, Any]]
        # 必填：已确认的业务条件。
        resolved_conditions: dict[str, Any]
        # 必填：剩余迭代、时间和资源限制。
        limits: dict[str, Any]

    @dataclass
    class ToolSpec:
        # 必填：工具注册名，与 NextAction.tool_name 对应。
        name: str
        # 必填：工具参数 Schema。
        argument_schema: dict[str, Any]
        # 必填：是否允许当前 Agent 调用。
        allowed_agent_types: list[str]
        # 必填：工具超时时间，单位秒。
        timeout_seconds: float
        # 必填：工具是否幂等、可以安全重试。
        idempotent: bool
        # 必填：工具说明；发送给 Planning Agent 的最小描述。
        description: str

## 2. 模块接口契约（Interface）

接口层只定义模块之间的可编程边界。PostgreSQL、Qdrant、Neo4j、LangGraph 和 LLM 客户端由应用组装层注入，四个核心模块不自行创建基础设施连接。

### 2.1 ContextEngine

    from typing import Protocol

    class ContextEngine(Protocol):
        async def build(
            self,
            request: ContextRequest,
            run_state: HarnessRunState,
        ) -> CompiledContext:
            # 1. 读取当前 HarnessRunState 和 Working Memory。
            # 2. 解析历史消息、上一轮 ToolResult、result_ref 和 asset_id。
            # 3. 生成记忆类型、作用域和查询词组成的召回计划。
            # 4. 调用 MemoryManager.retrieve() 获取所需记忆候选。
            # 5. 按配置调用业务知识或 Meta RAG。
            # 6. 合并候选，按来源和正文去重。
            # 7. 按类型策略、相关性、重要性、可信度、时效性和 token 预算选择。
            # 8. 较早历史需要时更新摘要；本轮超预算时执行压缩。
            # 9. 编译 messages、sections、resolved_asset_ids 和 trace。
            ...

### 2.2 MemoryManager

    class MemoryManager(Protocol):
        async def retrieve(
            self,
            request: MemoryRetrieveRequest,
        ) -> list[MemoryCandidate]:
            # 1. 校验 user_id、conversation_id、project_id 和 asset_ids 的访问边界。
            # 2. 读取 Working Memory；正式来源为 AgentState.messages 和 Checkpointer。
            # 3. 按 request.memory_types 路由 Semantic、Episodic、Perceptual。
            # 4. 每种类型内部执行 PostgreSQL 与 Qdrant/Neo4j 的混合召回。
            # 5. 合并同一记忆的多路信号并保留来源引用。
            # 6. 返回候选，不把底层数据库对象暴露给 ContextEngine。
            ...

        async def add(
            self,
            candidate: GovernedMemoryCandidate,
        ) -> MemoryWriteResult:
            # 1. 检查候选已经通过 Memory Formation 和 Governance。
            # 2. 在 PostgreSQL 中完成原子去重或版本替换。
            # 3. 事实提交成功后更新 Qdrant；Semantic 再更新 Neo4j。
            # 4. 投影失败记录到 PostgreSQL，不回滚已经提交的事实。
            ...

        async def register_asset(
            self,
            asset: MemoryAsset,
        ) -> MemoryAsset:
            # 登记附件身份、作用域、提取文本和索引状态，返回稳定 asset_id。
            ...

        async def get_asset(
            self,
            asset_id: str,
            user_id: str,
        ) -> MemoryAsset | None:
            # 按用户隔离读取附件，供历史 asset_id 引用解析。
            ...

        async def get_sources(
            self,
            memory_id: str,
            user_id: str,
        ) -> list[MemorySource]:
            # 读取长期记忆真实来源，供 ContextEngine trace 和结果引用。
            ...

### 2.3 PlanningAgent

    class PlanningAgent(Protocol):
        async def plan(
            self,
            context: CompiledContext,
            state_view: PlannerStateView,
            tool_specs: list[ToolSpec],
        ) -> NextAction:
            # 1. 接收 ContextEngine 编译出的 messages。
            # 2. 读取受控 PlannerStateView，不访问底层数据库和基础设施。
            # 3. 结合目标、已完成观察、限制和 ToolSpec 选择下一步动作。
            # 4. 使用结构化输出解析为 NextAction。
            # 5. Schema 错误和可重试模型错误抛给 Loop Controller。
            # 6. 不在 PlanningAgent 内部实现不可观测的工具执行循环。
            ...

### 2.4 ToolRuntime

    class ToolRuntime(Protocol):
        async def invoke(
            self,
            action: NextAction,
            run_state: HarnessRunState,
        ) -> ToolResult:
            # 1. 根据 action.tool_name 从 ToolRegistry 获取 ToolSpec。
            # 2. 校验参数、用户权限、当前状态、超时和资源限制。
            # 3. 注入 user_id、conversation_id、run_id 和幂等键。
            # 4. 调用 execute() 执行一个具体注册工具。
            # 5. 将异常分类为 TEMPORARY_ERROR 或 UNRECOVERABLE_ERROR。
            # 6. 生成 summary、result_ref、evidence_refs 和 limitations。
            # 7. 返回标准 ToolResult。
            ...

        async def execute(
            self,
            tool_name: str,
            arguments: dict[str, Any],
            run_state: HarnessRunState,
            timeout_seconds: float,
        ) -> ToolResult:
            # 1. 创建 ToolCallStatus.RUNNING 的调用记录。
            # 2. 执行一个具体工具实现。
            # 3. 记录耗时、资源使用和原始完成状态。
            # 4. 返回原始结果给 invoke() 统一归一化。
            ...

### 2.5 Memory 适配 DTO

    @dataclass
    class MemoryRetrieveRequest:
        # 必填：用户隔离键。
        user_id: str
        # 必填：用于召回和排序的问题。
        query: str
        # 必填：本轮允许召回的记忆类型组合。
        memory_types: list[MemoryType]
        # 可选：当前会话作用域。
        conversation_id: str | None = None
        # 可选：当前项目作用域。
        project_id: str | None = None
        # 可选：当前问题引用的附件。
        asset_ids: list[str] = field(default_factory=list)
        # 必填：每种记忆的初始召回上限。
        limit: int = 8

    @dataclass
    class MemoryCandidate:
        # 必填：Working 消息 ID 或长期记忆 ID。
        memory_id: str
        # 必填：记忆类型。
        memory_type: MemoryType
        # 必填：给 ContextEngine 使用的正文或摘要。
        content: str
        # 必填：类型内部混合召回后的综合分数。
        score: float
        # 必填：来源对象、来源路径和来源系统。
        source_refs: list[dict[str, Any]]
        # 可选：vector、lexical、graph、reference 等分项信号。
        signals: dict[str, float] = field(default_factory=dict)
        # 可选：fact_key、event_key、conditions、asset_id 等结构化数据。
        structured_data: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class GovernedMemoryCandidate:
        # 必填：经过 Governance 的长期候选类型。
        memory_type: MemoryType
        # 必填：可脱离当前对话理解的正文。
        content: str
        # 必填：用户隔离键。
        user_id: str
        # 必填：真实来源引用。
        source_refs: list[dict[str, Any]]
        # Semantic 的 fact_key 或 Episodic 的 event_key。
        identity_key: str | None = None
        # 同一事实在不同条件下成立时的条件。
        conditions: dict[str, Any] = field(default_factory=dict)
        # 附件、实体关系和类型扩展信息。
        structured_data: dict[str, Any] = field(default_factory=dict)
        # 必填：重要性，范围 0 到 1。
        importance: float = 0.5
        # 必填：可信度，范围 0 到 1。
        confidence: float = 0.7

## 3. 状态机与核心伪代码

### 3.1 Loop Controller 状态节点和转移

| 状态节点 | 进入条件 | 节点动作 | 成功转移 | 失败或其他转移 |
| --- | --- | --- | --- | --- |
| StartRun | Gateway 已完成鉴权、限流和请求校验 | 生成 turn_id、run_id、初始 HarnessRunState 并保存 Checkpointer | 新运行 -> RunState | 初始化或身份失败 -> Finalization |
| RestoreRun | 收到用户确认或恢复请求 | 按 run_id 读取 Checkpointer，校验 user_id、conversation_id 和等待状态 | 校验成功 -> RunState | 状态不存在、身份不匹配或不可恢复 -> Finalization |
| RunState | StartRun 或 RestoreRun 完成 | 持有当前完整运行现场并确定下一阶段 | 新一轮 -> InvokeContext | 状态不一致 -> Finalization |
| InvokeContext | 新运行、工具结果写回或确认恢复 | 调用 ContextEngine.build() | 成功 -> InvokePlanner | 临时失败且未超限 -> InvokeContext；否则 -> Finalization |
| InvokePlanner | ContextEngine 返回 CompiledContext | 组装 PlannerInput 并调用 Planning Agent | 返回结果 -> PlannerGate | 模型或 Schema 失败 -> PlannerRetry |
| PlannerGate | 收到 Planning Agent 输出 | 检查 NextAction 结构、动作类型和必填字段 | 有效 -> ValidateAction | 失败且未超限 -> PlannerRetry；连续失败 N 次 -> PlannerFailure |
| PlannerRetry | PlannerGate 失败或 Planner 调用失败且允许重试 | 增加 planner_retry_count，记录原因并保存状态 | 重新规划 -> InvokePlanner | 达到上限 -> PlannerFailure |
| PlannerFailure | planner_retry_count 超限 | 保存失败原因并生成受控结束状态 | -> Finalization | - |
| ValidateAction | NextAction 结构有效 | 检查工具注册、参数 Schema、权限、当前状态和资源约束 | tool_call -> InvokeTool；ask_user -> PauseRun；final_answer -> Finalization | invalid / unsupported -> InvalidAction |
| InvokeTool | ValidateAction 通过且动作是 tool_call | 调用 ToolRuntime.invoke() 执行一个工具 | 返回 ToolResult -> HandleToolResult | 异常必须转换为 ToolResult -> HandleToolResult |
| HandleToolResult | ToolResult 已返回 | 识别成功、部分成功、临时错误、需用户和不可恢复错误 | -> RecordObservation | 不丢弃结果，交由 ToolOutcomeGate 判断 |
| RecordObservation | 结果允许写回 | 保存 ToolResult、摘要、证据、限制和重试计数 | -> ToolOutcomeGate | 状态写入失败 -> Finalization |
| ToolOutcomeGate | 观察结果已保存 | 决定继续规划、重试、等待用户或结束 | success / partial -> ContinueGate；temporary -> RetryGate；needs_user -> PauseRun；unrecoverable -> Finalization | - |
| RetryGate | ToolResult.status 为 TEMPORARY_ERROR | 检查 retryable、幂等性、次数、deadline 和动作身份 | 允许 -> RetryTool | 超限或不可安全重试 -> Finalization |
| RetryTool | 原 action_id 可安全重试 | 增加 tool_retry_count，保留原动作和幂等键 | 原动作重试 -> InvokeTool | 计数保存失败 -> Finalization |
| InvalidAction | NextAction 不支持或参数非法 | 记录非法动作错误，作为观察反馈给下一轮规划 | 未超限 -> RecordObservation -> InvokeContext | planner_retry_count 超限 -> Finalization |
| ContinueGate | 成功或部分成功的 ToolResult 已写回 | 检查目标、依赖、取消、超时和迭代限制 | 继续 -> InvokeContext；完成 -> Finalization | 超时/取消/达到限制 -> Finalization |
| PauseRun | NextAction 为 ask_user，或工具返回 NEEDS_USER | 准备等待用户的原因、问题和证据 | -> PauseState | 状态无法准备 -> Finalization |
| PauseState | 等待信息已生成 | 写入 waiting_confirmation 和可恢复现场到 Checkpointer | -> ConfirmWait | 持久化失败 -> Finalization |
| ConfirmWait | waiting_confirmation 已持久化 | 返回确认请求并停止当前调用 | 用户输入 -> ResumeRun | 用户取消或等待超时 -> Finalization |
| ResumeRun | 用户确认接口完成鉴权 | 合并确认条件，清除等待字段并恢复同一个 run_id | -> RunState -> InvokeContext | 恢复校验失败 -> Finalization |
| Finalization | 完成、失败、取消、超时或重试超限 | 保存消息、结果、引用、轨迹、最终状态，并提交 Memory Formation | -> Response | 保存失败 -> 记录告警并返回受控错误 |

PlannerRetry、PlannerFailure、RetryGate 和 RetryTool 是 Loop Controller 的显式控制节点，不能被隐藏在 Planning Agent 或 Tool Runtime 的内部循环中。

### 3.2 控制器主循环伪代码

    PLANNER_RETRY_LIMIT = 2
    TOOL_RETRY_LIMIT = 2
    CONTEXT_RETRY_LIMIT = 2

    async def run_harness(request, restored_state=None):
        state = restored_state or await start_run(request)

        while True:
            if state.status is RunStatus.WAITING_CONFIRMATION:
                # 当前请求不能自动越过等待状态；确认接口负责调用恢复入口。
                return await persist_and_return(state)

            if state.iteration >= state.max_iterations:
                state = add_error(state, "RUN_ITERATION_LIMIT", "达到最大迭代次数")
                state.status = RunStatus.TIMEOUT
                return await finalize(state)

            if utcnow() >= state.deadline_at:
                state = add_error(state, "RUN_TIMEOUT", "达到运行截止时间")
                state.status = RunStatus.TIMEOUT
                return await finalize(state)

            state.iteration += 1
            state.phase = LoopPhaseStatusType.BUILD_CONTEXT
            await checkpoint(state)

            try:
                compiled = await context_engine.build(
                    context_request_from(state),
                    state,
                )
                state.context_build_id = compiled.build_id
                state.context_retry_count = 0
            except TemporaryError as exc:
                state = record_context_error(state, exc)
                if state.context_retry_count < CONTEXT_RETRY_LIMIT:
                    state.context_retry_count += 1
                    await backoff(state.context_retry_count)
                    await checkpoint(state)
                    continue
                return await finalize_with_error(state, "CONTEXT_BUILD_FAILED")
            except Exception as exc:
                return await finalize_with_error(state, "CONTEXT_BUILD_UNRECOVERABLE")

            state.phase = LoopPhaseStatusType.PLAN
            await checkpoint(state)
            try:
                action = await planning_agent.plan(
                    compiled,
                    planner_state_view(state),
                    await tool_registry.specs_for(state),
                )
            except (SchemaValidationError, TemporaryError) as exc:
                state = record_planner_error(state, exc)
                state.planner_retry_count += 1
                await checkpoint(state)
                if state.planner_retry_count <= PLANNER_RETRY_LIMIT:
                    await backoff(state.planner_retry_count)
                    continue
                state.status = RunStatus.FAILED
                return await finalize_with_error(state, "PLANNER_RETRY_EXHAUSTED")
            except UnrecoverableError as exc:
                return await finalize_with_error(state, "PLANNER_UNRECOVERABLE")

            state.next_action = action
            state.phase = LoopPhaseStatusType.VALIDATE_ACTION
            validation = await planner_gate.validate(action, state)
            await checkpoint(state)

            if not validation.valid:
                state = record_invalid_action(state, validation)
                state.planner_retry_count += 1
                await checkpoint(state)
                if state.planner_retry_count > PLANNER_RETRY_LIMIT:
                    return await finalize_with_error(state, "INVALID_ACTION_LIMIT")
                # 非法动作作为下一轮上下文中的观察反馈。
                continue

            if action.action_type is ActionType.ASK_USER:
                state = enter_confirmation_wait(state, action)
                await checkpoint(state)
                return await persist_and_return(state)

            if action.action_type is ActionType.FINAL_ANSWER:
                state.answer_content = action.answer_content
                state.report_ref = action.report_ref
                state.status = RunStatus.COMPLETED
                return await finalize(state)

            # 已校验的 tool_call 开始新动作，工具重试计数归零。
            state.tool_retry_count = 0
            while True:
                state.phase = LoopPhaseStatusType.EXECUTE_TOOL
                await checkpoint(state)
                result = await tool_runtime.invoke(action, state)
                state.phase = LoopPhaseStatusType.HANDLE_RESULT
                state.tool_results.append(result)
                state.phase = LoopPhaseStatusType.RECORD_OBSERVATION
                state.observations.append(observation_from(result))
                await checkpoint(state)

                if result.status in {ResultStatus.SUCCESS, ResultStatus.PARTIAL}:
                    state.planner_retry_count = 0
                    state.tool_retry_count = 0
                    break

                if result.status is ResultStatus.NEEDS_USER:
                    state = enter_confirmation_wait_from_result(state, result)
                    await checkpoint(state)
                    return await persist_and_return(state)

                if result.status is ResultStatus.TEMPORARY_ERROR:
                    if (
                        result.retryable
                        and state.tool_retry_count < TOOL_RETRY_LIMIT
                        and utcnow() < state.deadline_at
                    ):
                        state.tool_retry_count += 1
                        await backoff(state.tool_retry_count)
                        continue
                    return await finalize_with_error(state, "TOOL_RETRY_EXHAUSTED")

                # UNRECOVERABLE_ERROR 或未知状态均禁止盲目重复执行。
                return await finalize_with_error(
                    state,
                    result.error_code or "TOOL_FAILED",
                )

            # 工具成功或部分成功后回到 while 顶部，重新构建上下文并再次规划。

### 3.3 用户确认恢复伪代码

    async def resume_after_confirmation(resume_request):
        state = await load_checkpoint(resume_request.run_id)
        verify_identity(state, resume_request.user_id)
        require_status(state, RunStatus.WAITING_CONFIRMATION)

        state.input_text = resume_request.message
        state.confirmation_payload = resume_request.payload
        state.confirmation_status = ConfirmationStatus.CONFIRMED
        state.confirmation_question = None
        state.confirmation_reason = None
        state.status = RunStatus.RUNNING
        state.phase = LoopPhaseStatusType.BUILD_CONTEXT
        state.errors.append({"code": "USER_CONFIRMATION_RECEIVED"})
        await checkpoint(state)
        return await run_harness(resume_request.to_harness_request(), state)

## 4. 存储方案设计

### 4.1 LangGraph Checkpointer

#### 保存内容

LangGraph Checkpointer 保存当前线程可以继续运行所需的完整 AgentState / HarnessRunState：

- messages 和必要消息元数据。
- user_id、conversation_id、thread_id、turn_id 和 run_id。
- phase、status、iteration、deadline_at 和当前运行限制。
- PlannerStateView 所需的目标、计划、已完成项和观察摘要。
- 当前 NextAction、ToolResult 摘要、result_ref、evidence_refs 和 limitations。
- planner_retry_count、tool_retry_count 和 context_retry_count。
- waiting_confirmation 状态、confirmation_question、confirmation_reason 和确认现场。
- 最后一个可继续执行的 LangGraph 节点位置。

不在 Checkpointer 中无限保存：完整 SQL 结果、完整 Python 输出、大文件原文、完整 System Prompt、隐藏思考链、密钥和完整异常堆栈。大对象通过 result_ref、asset_id 或 report_ref 引用。

#### 保存时机

1. StartRun 创建初始状态后。
2. RestoreRun 成功恢复并完成身份校验后。
3. InvokeContext 前后，保存 phase、context_build_id 和上下文重试计数。
4. InvokePlanner 前后，保存 Planner 版本、planner_retry_count 和 NextAction。
5. InvokeTool 前，保存 tool_call_id、action_id、attempt 和当前状态。
6. ToolResult 返回并完成 RecordObservation 后。
7. RetryTool 增加 tool_retry_count 后。
8. PauseState 写入 waiting_confirmation 前。
9. ResumeRun 合并用户确认并重新规划前。
10. Finalization 保存最终状态。

当前项目已经由 AsyncPostgresSaver.setup() 管理 LangGraph 官方 Checkpointer 表。Harness 必须复用同一个 Checkpointer 和现有 thread_id，不重新定义、复制或重复创建官方表。

### 4.2 PostgreSQL 记忆事实

当前项目实际使用的长期记忆主表是 agent_memories，不另造 memory_facts 表。Working Memory 由 AgentState.messages 和 Checkpointer 承担，不写入 agent_memories。

#### agent_memories

| 字段 | PostgreSQL 类型 | 必填 | 设计要求 |
| --- | --- | --- | --- |
| memory_id | VARCHAR(128) | 是 | 主键；也是 Qdrant 业务 point 的来源 ID |
| user_id | VARCHAR(128) | 是 | 用户隔离键，任何长期检索都必须带上 |
| memory_type | VARCHAR(32) | 是 | semantic、episodic 或 perceptual |
| scope | VARCHAR(32) | 是 | user、conversation 或 project |
| conversation_id | VARCHAR(128) | 否 | 产生记忆的会话 ID |
| project_id | VARCHAR(128) | 否 | 项目作用域 ID |
| content | TEXT | 是 | 可检索和展示的长期记忆正文 |
| structured_data | JSONB | 是 | fact_key、event_key、conditions、asset_id、实体关系等 |
| status | VARCHAR(32) | 是 | active、superseded、forgotten 或 expired |
| importance | DOUBLE PRECISION | 是 | 重要性，范围 0 到 1 |
| confidence | DOUBLE PRECISION | 是 | 提取或用户确认可信度，范围 0 到 1 |
| version | INTEGER | 是 | 同一逻辑记忆的版本号 |
| supersedes_memory_id | VARCHAR(128) | 否 | 被当前版本替代的旧记忆 ID |
| expires_at | TIMESTAMP | 否 | 预设过期时间；空表示不自动过期 |
| access_count | INTEGER | 是 | 被检索命中的次数 |
| last_accessed_at | TIMESTAMP | 否 | 最近命中时间 |
| created_at | TIMESTAMP | 是 | 创建时间 |
| updated_at | TIMESTAMP | 是 | 更新时间 |

#### memory_sources

memory_sources 是来源关联表，不是第二套记忆正文。它至少保存：

- memory_id：指向 agent_memories。
- source_type：message、turn、output 或 asset。
- source_id：来源对象 ID。
- source_path：来源对象内部字段路径，可为空。
- created_at：来源关联时间。

它用于把一条长期记忆追溯到具体消息、轮次、结果或附件，并让 ContextEngine 的 trace 可以返回来源引用。

#### memory_assets

memory_assets 保存 Perceptual Memory 的附件身份和索引状态：asset_id、user_id、conversation_id、project_id、modality、file_name、mime_type、storage_uri、extracted_text、extraction_status、encoder_name、embedding_dimension、index_status 和 metadata。历史附件引用必须先按 asset_id 和 user_id 校验，再读取内容。

#### memory_graph_projections 和 memory_index_jobs

- memory_graph_projections 保存 Neo4j Semantic Memory 投影的实体、关系和同步状态，作为图投影重建输入。
- memory_index_jobs 只记录已经发生的 Qdrant 或 Neo4j 投影失败；当前不启动自动重试消费者。
- 两者都不是模型事实来源，也不应被 ContextEngine 直接读取。

#### 长期记忆写入顺序

    Finalization
        -> Memory Formation 提取候选
        -> Governance 校验来源、类型、权限、敏感信息和置信度
        -> MemoryManager.add()
        -> PostgreSQL 原子去重或版本替换
        -> Qdrant 向量投影
        -> Semantic Memory 的 Neo4j 关系投影

PostgreSQL 是长期记忆事实来源；Qdrant 和 Neo4j 是可删除、可重建的检索投影。投影失败不回滚已经提交的事实，但必须记录错误和关联 memory_id。

### 4.3 Qdrant 向量集合

#### 集合划分

采用按记忆类型和 Perceptual 模态拆分 collection 的方式：

| Collection | 内容 | 当前状态 |
| --- | --- | --- |
| memory_semantic | Semantic Memory 的事实、偏好和规则 | 启用 |
| memory_episodic | Episodic Memory 的任务、过程和结果 | 启用 |
| memory_perceptual_text | 文本附件的 Perceptual Memory | 当前启用 |
| memory_perceptual_image | 图片附件向量 | 预留 |
| memory_perceptual_audio | 音频附件向量 | 预留 |
| memory_perceptual_video | 视频附件向量 | 预留 |

不把三类长期记忆全部混在一个 collection 中再依赖调用方过滤。独立 collection 允许每种记忆使用不同排序策略、生命周期和 encoder；collection 内仍然必须使用 payload 完成用户和作用域隔离。

#### Payload

每个 collection 至少建立并索引以下 payload 字段：

    memory_id
    user_id
    memory_type
    scope
    conversation_id
    project_id
    modality
    asset_id
    status
    version

查询必须使用以下逻辑过滤：

    user_id = 当前用户
    status = active
    and (
        scope = user
        or scope = conversation and conversation_id = 当前会话
        or scope = project and project_id = 当前项目
    )

Qdrant 返回 memory_id 和向量分数后，必须回 PostgreSQL 读取有效事实并再次检查用户、作用域、状态和版本。向量相似度不能直接作为最终业务事实。

### 4.4 Neo4j Semantic 关系存储

Neo4j 只服务 Semantic Memory 的实体关系召回，不承载全部长期记忆，也不是唯一事实来源。

#### 节点结构

    (:Memory {
        memory_id,
        user_id,
        scope,
        conversation_id,
        project_id,
        status
    })

    (:MemoryEntity {
        entity_id,
        source_entity_id,
        name,
        entity_type,
        user_id
    })

#### 关系结构

    (:Memory)-[:MENTIONS {memory_id}]->(:MemoryEntity)
    (:MemoryEntity)-[:RELATION_TYPE {memory_id}]->(:MemoryEntity)

entity_id 使用 user_id 加 source_entity_id 的命名空间，避免不同用户的同名实体合并。关系带 memory_id，替换或遗忘一条 Semantic Memory 时可以按 memory_id 删除对应关系。

#### 约束和查询规则

- MemoryEntity.entity_id 唯一。
- Memory.memory_id 唯一。
- MemoryEntity.name 建立名称索引。
- MemoryEntity(user_id, source_entity_id) 建立组合索引。
- Memory(user_id, scope) 建立作用域索引。
- 图查询先按 user_id、status 和可见 scope 过滤。
- Neo4j 只返回关联 memory_id 和关系分数，正文回 PostgreSQL 获取。
- 没有有效实体的 Semantic Memory 不创建不可检索的空图节点。

## 5. 异常处理与重试策略

### 5.1 建议阈值

| 计数器 | 建议值 | 统计范围 | 超限处理 |
| --- | ---: | --- | --- |
| planner_retry_count | 2 次重试 | 当前 run_id；包含 Planner 失败和非法动作 | 进入 Finalization，返回规划失败 |
| tool_retry_count | 2 次重试 | 当前 action_id | 保留已有 ToolResult，进入 Finalization |
| context_retry_count | 2 次重试 | 当前 ContextEngine 构建过程 | 不使用空上下文调用模型，受控结束 |
| max_iterations | 8 次迭代 | 当前 run_id | 标记超限并受控结束 |

“2 次重试”表示首次执行失败后最多再执行两次，总尝试次数最多为 3 次。所有阈值集中配置，不得散落在业务节点中。

### 5.2 临时错误判定

下列情况可以归类为 Temporary Error，但仍必须同时检查动作是否安全、是否幂等：

- LLM、Embedding、Qdrant、Neo4j 或其他依赖的网络超时。
- 服务暂时不可用、连接池耗尽或明确返回 5xx。
- 限流、排队或响应明确要求稍后重试。
- 工具执行超时，且没有外部副作用或重复执行风险。
- Planning Agent 的暂时性模型调用失败。
- Checkpointer 的瞬时连接失败，且当前状态仍可确认没有提交冲突。

临时错误使用指数退避和随机抖动，例如 0.5 秒、1 秒、2 秒，同时不能越过 deadline_at。

### 5.3 不可恢复错误判定

下列情况禁止盲目重试：

- tool_name 未注册、动作结构无法修复或参数 Schema 不合法。
- 用户无权访问会话、项目、附件、数据或工具。
- SQL、Python、文件或资源参数违反安全策略。
- 指标口径冲突或必要条件缺失，需要用户确认。
- 数据库表、字段、配置或凭证不存在或无效。
- Checkpointer 无法恢复可信状态，或身份校验失败。
- 工具已经产生外部副作用，但没有可证明的幂等机制。
- 程序代码异常、数据结构损坏或返回状态未知。

指标口径冲突不是普通失败：转换为 ResultStatus.NEEDS_USER，进入 PauseRun，保存冲突证据并等待用户确认。

### 5.4 工具重试前置条件

只有以下条件全部满足时，RetryGate 才能进入 RetryTool：

    result.status == ResultStatus.TEMPORARY_ERROR
    result.retryable is True
    state.tool_retry_count < TOOL_RETRY_LIMIT
    utcnow() < state.deadline_at
    action_id 没有被 Planning Agent 替换
    ToolSpec.idempotent is True 或调用使用相同 idempotency_key

推荐使用 run_id、action_id 和 attempt 组成调用追踪键。只读查询通常可以安全重试；存在外部写副作用的工具必须显式声明幂等策略。

### 5.5 超限后的降级

| 失败位置 | 降级处理 |
| --- | --- |
| Planning Agent | 保存最后 context_build_id 和错误原因，进入 Finalization，返回无法形成可靠计划 |
| PlannerGate | 保留非法动作的错误反馈；若达到规划上限则受控结束 |
| Tool Runtime | 保留已有 ToolResult、result_ref 和 limitations；证据足够时允许返回部分结果 |
| ContextEngine | 不使用空上下文继续调用模型，进入 Finalization 并返回上下文构建失败 |
| Checkpointer | 无法确认状态一致性时停止继续执行，避免重复调用工具 |
| Qdrant / Neo4j | PostgreSQL 事实保持有效，记录 memory_index_jobs，不影响已经生成的回答 |
| 用户确认等待 | 保持 waiting_confirmation；用户恢复或等待期限到达后再结束 |

## 6. 可观测性埋点清单

### 6.1 Traces / Spans

每次 Harness Run 建立一条根 Trace，trace_id 与 run_id 关联。Span 属性禁止写入完整用户原问题、完整 prompt、完整 SQL、密钥和隐藏思考链。

| Span 名称 | 覆盖节点 | 必要属性 |
| --- | --- | --- |
| harness.run | StartRun 到 Finalization | run_id、user_id_hash、conversation_id、最终状态、迭代数、总耗时 |
| harness.restore | RestoreRun | run_id、thread_id、恢复结果、状态版本 |
| context.build | ContextEngine.build() | build_id、agent_type、候选数、入选数、token_budget、final_token_count、耗时 |
| context.resolve_references | ResolveReferences | 历史引用数、asset 数、未解析引用数、耗时 |
| context.retrieve_memory | PlanRetrieval 到 MemoryManager 返回 | memory_type、scope、召回数、耗时、状态 |
| context.retrieve_rag | 业务知识或 Meta RAG 调用 | 是否启用、召回数、耗时、状态 |
| context.summarize | 较早历史摘要更新 | 覆盖消息数、摘要 token、是否更新、耗时 |
| context.compress | 本轮候选压缩 | 原始 token、最终 token、压缩候选数、耗时 |
| planner.call | InvokePlanner | planner_version、输入 token、输出 token、动作类型、重试次数、耗时 |
| planner.validate | PlannerGate 和 ValidateAction | action_type、tool_name、校验结果、拒绝原因码 |
| tool.invoke | InvokeTool | tool_call_id、action_id、tool_name、attempt、status、耗时 |
| tool.execute | ToolRuntime.execute() | tool_name、超时、资源使用、结果状态 |
| result.normalize | HandleToolResult | result_ref 是否生成、证据数、限制数、错误码 |
| harness.pause | PauseRun 到 ConfirmWait | 等待原因、confirmation_status、保存结果 |
| harness.resume | ResumeRun | run_id、身份校验结果、确认结果 |
| memory.formation | Finalization 提交形成任务 | trigger、候选数、接受数、拒绝数、失败数 |
| harness.finalization | Finalization | 最终状态、输出类型、持久化结果、错误码、耗时 |

### 6.2 Metrics

指标名采用稳定的 snake_case。run_id、turn_id、原始问题、完整 SQL、完整参数 JSON 和 result_ref 不能作为 Metrics label，以免产生高基数和敏感信息泄露。

| 指标名称 | 类型 | 建议维度 | 说明 |
| --- | --- | --- | --- |
| harness_run_total | Counter | status、agent_type | Harness Run 次数 |
| harness_run_duration_seconds | Histogram | status、agent_type | 一次 Run 总耗时 |
| harness_iteration_total | Counter | agent_type、outcome | 内部迭代次数 |
| harness_active_runs | Gauge | status | 当前运行数 |
| context_build_total | Counter | status、agent_type | 上下文构建次数 |
| context_build_duration_seconds | Histogram | status、agent_type | 上下文构建耗时 |
| context_candidates_total | Counter | source_kind、agent_type | Gather 候选数量 |
| context_selected_total | Counter | source_kind、agent_type | 最终入选数量 |
| context_token_usage | Histogram | agent_type、stage | 上下文 token 使用量 |
| context_compression_total | Counter | reason、source_kind | 压缩次数 |
| context_summary_update_total | Counter | agent_type | 摘要更新次数 |
| memory_retrieval_total | Counter | memory_type、source、status | 记忆召回次数 |
| memory_retrieval_duration_seconds | Histogram | memory_type、source | 记忆召回耗时 |
| memory_candidates_total | Counter | memory_type、outcome | 记忆候选数量 |
| planner_call_total | Counter | status、agent_type | Planning Agent 调用次数 |
| planner_duration_seconds | Histogram | status、agent_type | Planner 调用耗时 |
| planner_token_usage | Histogram | direction、agent_type | Planner 输入和输出 token |
| planner_retry_total | Counter | reason、agent_type | Planner 重试次数 |
| planner_invalid_action_total | Counter | reason、agent_type | 非法动作次数 |
| tool_call_total | Counter | tool_name、status | 工具调用次数 |
| tool_call_duration_seconds | Histogram | tool_name、status | 工具执行耗时 |
| tool_retry_total | Counter | tool_name、reason | 工具重试次数 |
| tool_result_total | Counter | tool_name、status | ToolResult 状态数量 |
| tool_timeout_total | Counter | tool_name | 工具超时数量 |
| confirmation_wait_total | Counter | reason、outcome | 用户确认等待次数 |
| confirmation_wait_duration_seconds | Histogram | outcome | 用户等待时长 |
| checkpoint_write_total | Counter | status、phase | Checkpointer 写入次数 |
| checkpoint_write_duration_seconds | Histogram | status、phase | Checkpointer 写入耗时 |
| memory_formation_total | Counter | trigger、status | 长期记忆形成次数 |
| memory_formation_candidate_total | Counter | memory_type、action | 候选治理结果 |
| harness_error_total | Counter | node、error_code、error_class | 受控错误数 |

### 6.3 Logs

所有节点输出结构化 JSON 日志。每条与一次运行相关的日志至少包含以下字段：

    {
      "timestamp": "2026-09-04T12:00:00Z",
      "level": "INFO",
      "service": "data-agent",
      "trace_id": "trace-id",
      "span_id": "span-id",
      "run_id": "run-id",
      "turn_id": "turn-id",
      "conversation_id": "conversation-id",
      "user_id_hash": "hashed-user-id",
      "node": "tool.invoke",
      "phase": "execute_tool",
      "status": "success",
      "duration_ms": 842,
      "iteration": 2,
      "attempt": 1,
      "action_type": "tool_call",
      "tool_name": "query_data",
      "context_build_id": "build-id",
      "memory_id": null,
      "error_code": null,
      "error_class": null,
      "result_ref": "result-id",
      "message": "tool execution completed"
    }

必须记录：

- timestamp、level、service。
- trace_id、span_id、run_id、turn_id、conversation_id。
- user_id_hash；普通日志不直接写用户敏感身份。
- node、phase、status、duration_ms。
- iteration、attempt、action_type 或 tool_name。
- context_build_id、memory_id、result_ref 等稳定引用。
- error_code、error_class 和脱敏错误说明。

禁止记录：

- API Key、密码、Token、私钥和数据库连接字符串。
- 完整 System Prompt、隐藏思考链、完整 SQL 和完整工具参数。
- 未脱敏的个人信息、完整用户原问题和完整工具原始输出。

## 7. 编码落地顺序

1. 定义 HarnessRunState、NextAction、ToolResult、CompiledContext 和相关枚举。
2. 定义 ContextEngine、MemoryManager、PlanningAgent、ToolRuntime、ToolRegistry 和 PlannerGate 的 Protocol。
3. 实现 Loop Controller 的 StartRun、RestoreRun、InvokeContext、InvokePlanner、PlannerGate 和 Finalization。
4. 将现有 app/agent/context_engine/ 适配到 ContextEngine.build()。
5. 将现有 Query Agent 等能力注册为 Tool Runtime 的 ToolSpec。
6. 实现 ToolResult 归一化、result_ref 和观察结果写回 HarnessRunState。
7. 复用现有 LangGraph Checkpointer，验证暂停、恢复和同一个 run_id 的连续性。
8. 实现 ask_user、final_answer 和工具结果后的上下文重建。
9. 接入 Traces、Metrics 和结构化 Logs。
10. 用真实多轮数据问题验证“工具结果 -> 状态更新 -> ContextEngine 重建 -> Planning Agent 再规划”的闭环。

## 8. 验收标准

- 新请求能够从 StartRun 进入 ContextEngine、Planning Agent、PlannerGate 和 Tool Runtime。
- 每个 ToolResult 都能通过 action_id、tool_call_id 和 run_id 关联到原始动作。
- 工具结果写回后，下一次 Planning Agent 调用使用新的 CompiledContext，而不是旧上下文。
- Planner 失败最多重试 2 次，工具临时失败最多重试 2 次，且所有计数都能从 Checkpointer 恢复。
- ask_user 能保存 waiting_confirmation 状态，用户确认后恢复同一个 run_id。
- final_answer 能完成消息、结果、引用、轨迹、Checkpoint 和 Memory Formation 的收口。
- PostgreSQL、Qdrant 和 Neo4j 的用户、会话、项目作用域不会互相越权。
- Qdrant 或 Neo4j 投影失败不会伪装成事实写入失败，也不会丢失 PostgreSQL 事实。
- Trace、Metrics 和 Logs 至少覆盖运行开始、上下文构建、规划、动作校验、工具调用、暂停恢复和最终收尾。
