# 数据代理 Harness 模块化增量技术设计文档

> 基线：2026-09-10 当前工作树。本文为增量设计，不表示 Harness 已实现。

## 1. 文档目标与范围

目标是在保留现有 Data Agent 能力的前提下，引入以 Loop Controller 为唯一调度中心的 Harness，支持内部循环、暂停恢复、重试、取消、最终收尾和可观测性。本次只生成设计，不修改业务代码。新增类、函数和路径均为设计建议；判断使用“现有能力”“需要重构”“需要新增”“暂不启用”“待验证”。

## 2. 已验证的现有代码基线

### 2.1 状态、图和身份

**现有能力**：`app/agent/state.py::AgentState` 是 `TypedDict(total=False)`，包含身份字段 `input_text`、`user_id`、`conversation_id`、`thread_id`、`turn_id`、`run_id`、`asset_ids`、`messages`，以及路由、分析、问数、报告、SQL 和文本输出字段。当前工作树还包含 `harness: HarnessControlState`；只有 `messages: Annotated[list[AnyMessage], add_messages]` 使用 reducer，旧节点通过 `state.get()` 读取并返回字典增量。当前 `AgentState` 和 `AgentRunRequest` 均没有 `project_id`；目标 Harness 若要传递项目范围，需要新增一个扁平可选字段，未提供时保持 `None`。

**现有能力**：`app/agent/harness/contracts.py` 已有 `HarnessStatus`、`LoopPhase`、`ActionType`、`ResultStatus`、`ErrorCategory` 及一组 Pydantic DTO；`app/agent/harness/state.py` 已有默认控制状态和基础转换函数。当前能力尚未覆盖 schema version、身份恢复校验、终态前的 finalization 边界和 checkpoint DTO 校验。

**需要重构**：不能整体替换 `AgentState`，应在现有 `harness` 字段上补齐统一状态契约，旧业务字段保持扁平。

`app/agent/graph.py::build_agent_graph()` 是固定一次执行图：`route_question` 分到日常聊天、问数流、分析流或澄清边界；最终进入 `finalize_turn`。没有 Harness 级回边、interrupt、暂停或恢复协议。

`app/agent/query_graph.py` 是可复用子图，真实顺序为：关键词抽取；columns/tables/metrics/dimension values 四路召回；合并；指标/表过滤；一致性校验；补充上下文；生成 SQL；执行 SQL；增强结果。它不是独立的 `query_data()` 函数。

`AgentService._new_identity()` 当前固定 `thread_id == conversation_id`，每次新请求生成 `turn_id` 和 `run_id`；`_new_turn_state()` 清空临时字段。恢复同一 Harness Run 时不得重新调用新 turn 初始化。

### 2.2 ContextEngine

**现有能力**：`app/agent/context_engine/engine.py::ContextEngine.build(request: ContextRequest) -> CompiledContext` 是当前 ContextEngine 的真实入口。它会校验请求，记录 `start_build`，读取 Working Memory，执行指代解析和召回计划，收集候选、去重、选择、补充来源并编译消息；成功后记录 `finish_build`，失败时记录 `fail_build`、保留应用日志并重新抛出原异常。当前实现没有 `start_new_run()`、`restore_run()` 或 Harness 状态写入职责。

`ContextRequest` 的真实字段为 `user_id`、`conversation_id`、`query`、`system_instructions`、`agent_type`、`project_id`、`asset_ids`、`memory_types`、`enable_rag`、`token_budget`。现有 `CompiledContext`、`ContextSections`、`ContextBuildTrace` 位于 `app/agent/context_engine/contracts.py`，必须复用。

当前流程是 `start_build -> load_working -> resolve -> plan -> history.prepare -> gather -> deduplicate -> select -> enrich -> compile -> finish_build`。已具备 Working、摘要、Semantic/Episodic/Perceptual、附件及可选 RAG 的读取，以及引用解析、去重、评分、压缩、token 预算和不含正文的构建 trace。`PostgresContextStore` 保存摘要和 `context_build_runs`。当前主 Agent 图尚未调用它。

**需要新增**：在现有 `ContextRequest` 中增加明确的可选 `runtime_context` 字段，而不是创建第二套 `CompiledContext`。`RuntimeContext` 是 ContextEngine 输入边界的只读投影，具体 DTO 设计放在模块 2。

字段流向：`AgentState.input_text -> ContextRequest.query`；身份和附件 -> `ContextRequest`；`HarnessControlState -> RuntimeContext`；`ContextEngine.build() -> CompiledContext`；`CompiledContext.messages -> PlannerInput`。每次工具完成后重新 build，因为工具结果是下一次规划的新证据。

### 2.3 Memory

**现有能力**：`app/agent/memory/manager.py::MemoryManager` 有 `add`、`search`、`search_many`、`load_working`、`get_asset`、`get_sources`、`forget`、`update`、`register_asset`。不存在 `MemoryManager.retrieve()`。

`MemoryContextReader` 位于 `app/agent/memory/interfaces.py`，只提供搜索、附件、Working 和来源读取。Working loader 通过 Checkpointer 读取 `graph.aget_state({'configurable': {'thread_id': conversation_id}})`，并检查 `state.user_id`。

`MemoryFormationService.submit(TurnMemoryInput)` 的链路为 Eligibility -> Extractor -> Governance -> MemoryWriter -> `MemoryManager.add()` -> PostgreSQL `write_managed()` -> Qdrant/Neo4j 投影。显式“记住”同步处理，自动形成使用进程内后台任务，不是持久任务队列。PostgreSQL 是事实源，Qdrant 是向量投影，Neo4j 只服务 Semantic Memory；Perceptual Memory 当前只有 text 提取。

**需要重构**：Formation 提交没有持久幂等键；Finalization 必须避免同一 `turn_id` 重复提交。必须保留 `Finalization -> MemoryFormationService.submit() -> MemoryManager.add()` 边界，不能直接写 Manager。

### 2.4 问数、分析、报告、历史和 Checkpointer

当前不存在独立 `query_data()`、`analyze_data()` 或 `build_report()`。问数由 `query_graph` 承载；分析由 `execute_analysis` 按 `depends_on` 分层并行调用 Query Agent、生成数据画像、执行受限 Python 和 Sandbox；报告由 `generate_report_plan` 和 `render_report` 完成。

`execute_sql` 当前没有显式只读 SQL 校验、statement timeout 或结果行数上限。`python_sandbox.py` 是实验性隔离边界，不应升级为通用脚本执行器。

`ConversationRepository.start_turn()` 创建轮次和用户消息并设置 `active_run_id`；`finish_turn()` 写助手消息、结构化输出、完成时间并清除 active run。它不支持 `waiting_confirmation`，也不是天然幂等。`AgentService._submit_memory_formation()` 仅在历史保存成功后提交 Formation。

`app/clients/postgres_client.py` 使用 `AsyncPostgresSaver.from_conn_string()`、`await checkpointer.setup()`、`build_agent_graph(checkpointer=checkpointer)`；Harness 必须复用同一 Saver、同一 `thread_id` 和官方表，不创建第二套 LangGraph Checkpointer 表。

## 3. 流程图和当前代码的差异

```text
创建/恢复 -> ContextEngine.build -> PlanningAgent -> 校验 NextAction
tool_call -> ToolRuntime -> ToolResult -> RunObservation -> 重新 build
ask_user -> 持久化 waiting_confirmation -> 同一 run 恢复 -> 重新 build
final_answer -> Finalization -> 会话收尾 -> MemoryFormationService.submit
```

当前固定图没有内部回边；澄清分支直接 `finalize_turn`；ContextEngine 未接主链；分析内部 while 只调度静态任务，不重新规划；企业知识库没有真实独立实现。目标设计中的完整 `HarnessRunState` 改为现有 `AgentState + HarnessControlState`，避免重复状态。

## 4. 当前架构与目标 Harness 的差距

| 目标能力 | 当前代码状态 | 差距 | 处理方式 |
| --- | --- | --- | --- |
| HarnessGraphState | 现有 AgentState 已包含可选 `harness` 字段 | 控制状态契约、恢复校验和终态边界不完整 | 扩展并统一现有状态 |
| CompiledContext | 已存在但未进主 Loop | 未接入 | 直接复用 |
| Planning Agent | **现有能力**：有路由/分析 Planner | **需要新增**：统一 `NextAction` 契约 | 新增 |
| Tool Runtime | **现有能力**：工具逻辑分散在节点和子图 | **需要新增**：统一协议、注册和分发 | 新增并提取必要 Service |
| Loop Controller | 当前未发现 Harness 级调度中心 | **需要新增**：循环、重试、暂停和终止控制 | 新增 |
| Pause/Resume | **现有能力**：澄清分支结束 | **需要新增**：可恢复运行现场和恢复协议 | 新增 |
| Finalization | **现有能力**：`AgentService` 与 `finalize_turn` 分担收尾 | **需要重构**：统一暂停、幂等和终态收尾 | 重构并复用 |
| Memory Formation | **现有能力**：Formation 链路已存在 | **需要重构**：由 Harness Finalization 统一触发 | 复用 `submit` |
| Checkpointer | **现有能力**：`AsyncPostgresSaver` 已存在 | **需要重构**：统一 Harness 图的状态来源；不新建表 | 复用 |
| Meta RAG | **现有能力**：数据目录检索 | **已确认边界**：不是企业知识库 | 保留为 Data Catalog |
| 企业知识库 | 未发现独立实现 | 不能假设存在 | 暂不启用 |

## 5. 核心设计原则与职责边界

**需要重构/需要新增的统一约束**：保留现有 AgentState 与 messages reducer；只有 M5 推进控制状态。M2 编译上下文、M3 提出动作、M4 执行工具、M6 收尾并提交 Formation，各自不得越界。优先复用正式接口，只有旧节点与新工具确实共享业务逻辑时才提取 Service，不机械增加字段复制 Adapter。外部客户端由应用注入。Checkpointer 保存可恢复状态，业务 PostgreSQL 保存事实与协调账本，Qdrant/Neo4j 只做投影。当前不做用户级工具权限校验，运行身份一致性和结果隔离仍必须保留。
### 正式接口汇总

以下接口以各模块中的完整定义为准，本表用于检查跨模块调用方向，不另建同名 DTO 或薄 Adapter。

| 接口 | 正式签名 | 调用者 | 实现者 | 失败边界与 Mock 方式 |
| --- | --- | --- | --- | --- |
| ContextEngine | `build(ContextRequest) -> CompiledContext` | M5 | 现有 `ContextEngine`，M2 扩展请求 | 原异常映射为 `RunError(CONTEXT)`；单测注入 Fake builder |
| PlanningAgent | `plan(PlannerInput, issuance=ActionIssuanceContext) -> NextAction` | M5 | M3 | 失败抛 `PlannerFailure(RunError)`；Fake 返回三类动作 |
| ActionCommitter | `commit(ActionCommitRequest) -> ActionCommitResult` | M5 | M3/M5 协调层 | 并发或摘要冲突抛 `ActionCommitFailure(CONFLICT)`；Fake 模拟崩溃点 |
| ToolRegistry | `get(name) -> Tool`、`list_specs() -> tuple[ToolSpec, ...]` | M3/M4/M5 | M4 | 未注册或禁用工具返回稳定校验错误；单测使用本地 Registry |
| Tool | `execute(ToolCall, request=ToolExecutionRequest, dependencies=AgentContext) -> ToolHandlerResult` | M4 Dispatcher | M4 各高层工具 | handler 只抛可归一化异常；Fake 不连接外部依赖 |
| ToolRuntime | `execute(ToolExecutionRequest) -> ToolResult` | M5 | M4 | 所有错误归一化为 `ToolResult` 或运行冲突；Fake 按 attempt 返回结果 |
| LoopController | `start/restore/resume/cancel(...) -> LoopRunResult` | HarnessRunner | M5 | `restore` 只处理进程恢复，`resume` 只处理确认恢复；只返回暂停或终态 |
| FinalizationService | `finalize(FinalizationInput) -> FinalizationResult`、`reconcile(HarnessRunRef) -> FinalizationResult` | M5 | M6 | 主收尾失败抛受控 Finalization 错误；终态 checkpoint 对账不回到 M5；Formation 失败写独立状态 |
| MemoryFormationService | `submit(TurnMemoryInput) -> MemoryFormationResult` | M6 | 现有服务，M6 增加幂等 | Fake 返回 pending/skipped/failed；不得绕过 Governance |

`ActionCommitResult` 只证明动作已经提交，不携带完整 `NextAction`；M5 使用同一个已校验 `next_action` 做后续分派。`ToolHandlerResult` 只在 M4 内部存在；跨模块只传 `ToolResult`。`CompiledContext` 仍是现有 ContextEngine 的唯一输出。

## 6. 核心 DTO 和枚举

**现有能力**：`app/agent/harness/contracts.py` 已有 `StrEnum`、Pydantic 基础 DTO 和 `AgentState` 所需的 Harness 枚举；`AgentState` 继续保持 TypedDict。**需要重构**：补齐统一字段约束、checkpoint schema version、恢复校验和状态转换。Checkpoint 只保存可恢复的控制状态和必要业务字段，写入前使用 `model_dump(mode="json")`，恢复时使用 `model_validate`。禁止以无语义裸 `dict` 作为跨模块接口。

### 6.1 状态落点与字段所有权
`HarnessGraphState` 是 LangGraph 节点边界的组合状态；`HarnessControlState` 只承载 Harness 控制信息；现有 `AgentState` 的业务字段继续由旧节点读写。`CompiledContext`、`NextAction`、`ToolResult` 和 `FinalizationResult` 是单次调用或事件 DTO，不回写成同名状态字段。

| 字段 | 所有者 | 写入时机 | 约束 |
| --- | --- | --- | --- |
| `status`, `phase`, `iteration` | Loop Controller | 每次阶段转换 | 只能通过状态转换函数修改 |
| `action_seq` | Loop Controller/Harness 状态层 | 正式动作通过全部校验并提交时 | 保存最近一次已提交的动作序号；校验失败不消耗序号，通过动作记录与 checkpoint 协调提交 |
| `original_goal` | Run 初始化 | 创建新 run | resume 不覆盖 |
| `plan_progress` | Planning/Loop Controller | 规划完成或动作完成 | 仅保存摘要、依赖和完成标记，不保存隐藏思考 |
| `observations` | Tool Runtime/Loop Controller | 工具完成后 | 保存引用、摘要、状态和哈希；大结果放 Artifact |
| `pending_confirmation` | Loop Controller | 进入暂停时 | 只允许一个 pending confirmation |
| `last_error`、重试计数 | Loop Controller | 受控失败时 | 按 `action_id` 或阶段计数，不能无限重试 |
| `messages` 及现有分析/问数/报告字段 | 现有 AgentState/旧节点 | 业务节点执行时 | 不因 Harness 引入平行字段 |

### 6.2 枚举和不变量
只有 `running` 状态可以执行规划或工具动作；`waiting_confirmation` 必须保持暂停，所有终态都不可继续执行动作。统一 `NextAction` 契约在 M3 定义 `tool_call`、`ask_user` 与 `final_answer` 三种动作，三者必须严格互斥：`tool_call` 必须包含完整 `ToolCall`，`ask_user` 必须包含结构化确认请求，`final_answer` 必须包含非空文本。M3 只负责定义和校验 `ask_user`，不启用真实暂停、Checkpoint 和恢复 API；这些能力由 M5 接管并单独验收。所有 ID 在 DTO 中使用非空字符串，`iteration >= 0`，重试计数不得小于 0。

`action_seq` 是 Harness 状态层维护的单调递增序号，表示最近一次已经通过结构、工具、参数、能力和答案资格校验并提交的正式动作；它不是 LLM 输出字段。Loop Controller 根据 `state.harness.action_seq + 1` 计算候选序号，校验失败、Planner 异常或重试不会推进已提交的 `action_seq`。M3 的 `ActionNormalizer` 只能生成带候选序号的待提交 `NextAction`；M5 的动作提交边界必须再次检查期望序号，并通过 `prepared -> checkpoint -> committed` 协议协调提交 `action_seq`、正式动作记录和 checkpoint。`tool_call.action_id` 必须由 `run_id`、`iteration` 和本次候选 `action_seq`（提交后成为正式序号）通过 `ActionIdFactory` 生成，不能由模型提供或覆盖。同一 `run_id` 的并发规划必须由运行锁、租约 fencing 或带版本条件的原子更新串行化，不能让两个动作取得同一序号。

### 6.3 最小 DTO 契约
以下是核心运行闭环的最小协议示例。M1冻结运行请求、状态快照、计划进度、观察、错误和确认边界；具体工具参数 schema 由模块 4 的 Tool Registry 绑定，确认触发和 Finalization DTO 的调用流程留给对应模块实现。**需要重构**：当前 `app/agent/harness/contracts.py::PlannerInput` 仍使用 `context: Any`，目标契约将其收紧为现有 `CompiledContext`，字段名统一为 `compiled_context`；当前实现不应被误认为已经完成。
```python
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.context_engine.contracts import CompiledContext
from app.agent.harness.contracts import ActionType, ErrorCategory, ResultStatus
from app.agent.harness.contracts import PlanProgress, RunError, RunObservation


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunExecutionFence(ContractModel):
    """一次 Worker 执行权的短期凭证；不是用户授权凭证。"""

    owner_id: str = Field(min_length=1, max_length=128)
    fencing_token: int = Field(ge=1)
    lease_expires_at: datetime


class PlannerStateView(ContractModel):
    original_goal: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    plan_progress: PlanProgress = Field(default_factory=PlanProgress)
    observations: list[RunObservation] = Field(default_factory=list)
    last_error: RunError | None = None


class ToolSpec(ContractModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    permission: str = Field(min_length=1)
    version: str = Field(default="v1", min_length=1)
    enabled: bool = True
    timeout_seconds: int = Field(default=60, gt=0)
    idempotency: Literal[
        "idempotent",
        "conditionally_idempotent",
        "non_idempotent",
    ] = "idempotent"
    result_kind: Literal["inline_summary", "artifact", "report"] = "artifact"


class PlannerInput(ContractModel):
    compiled_context: CompiledContext
    state_view: PlannerStateView
    tool_specs: tuple[ToolSpec, ...]


class ToolCall(ContractModel):
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0)


class AskUserRequest(ContractModel):
    question: str = Field(min_length=1, max_length=2_000)
    reason_code: Literal[
        "missing_condition",
        "ambiguous_reference",
        "conflicting_definition",
        "tool_needs_user",
    ]
    required_fields: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def validate_required_fields(self) -> "AskUserRequest":
        if any(not field.strip() for field in self.required_fields):
            raise ValueError("required_fields 不能包含空字段名")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields 不能重复")
        return self


class NextAction(ContractModel):
    # 由 Harness 在动作通过校验后分配；不是 LLM 输出字段。
    action_seq: int = Field(ge=1)
    action_type: ActionType
    tool_call: ToolCall | None = None
    ask_user: AskUserRequest | None = None
    final_answer: str | None = Field(default=None, min_length=1, max_length=20_000)
    rationale_summary: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_payload(self) -> "NextAction":
        payload_count = sum(
            value is not None for value in (self.tool_call, self.ask_user, self.final_answer)
        )
        if payload_count != 1:
            raise ValueError("NextAction 必须且只能包含一个动作 payload")
        if self.action_type is ActionType.TOOL_CALL:
            if self.tool_call is None:
                raise ValueError("tool_call 动作必须只包含 tool_call")
        elif self.action_type is ActionType.ASK_USER:
            if self.ask_user is None:
                raise ValueError("ask_user 动作必须包含 ask_user")
        elif self.action_type is ActionType.FINAL_ANSWER:
            if not self.final_answer:
                raise ValueError("final_answer 动作必须只包含非空 final_answer")
        return self


class PlannerCapabilities(ContractModel):
    allow_tool_call: bool = True
    allow_ask_user: bool = False
    allow_final_answer: bool = True
    allow_context_only_final_answer: bool = False


class ToolResult(ContractModel):
    # tool_call_id 是 ToolCall.action_id 的工具协议别名，不是第二个身份。
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    summary: str = Field(min_length=1, max_length=4_000)
    result_ref: str | None = Field(default=None, min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error_category: ErrorCategory | None = None
    error_code: str | None = Field(default=None, min_length=1)
    error_message: str | None = Field(default=None, min_length=1)
    retryable: bool = False
    confirmation_request: AskUserRequest | None = None
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "ToolResult":
        error_values = (
            self.error_category,
            self.error_code,
            self.error_message,
        )
        has_complete_error = all(value is not None for value in error_values)
        has_partial_error = any(value is not None for value in error_values)
        error_status = self.status in {
            ResultStatus.TEMPORARY_ERROR,
            ResultStatus.NEEDS_USER,
            ResultStatus.UNRECOVERABLE_ERROR,
        }
        if has_partial_error and not has_complete_error:
            raise ValueError("ToolResult 错误字段必须同时存在")
        if has_complete_error != error_status:
            raise ValueError("ToolResult 的错误字段必须与 status 一致")
        if self.status is ResultStatus.NEEDS_USER and self.confirmation_request is None:
            raise ValueError("needs_user 结果必须携带 confirmation_request")
        if self.status is not ResultStatus.NEEDS_USER and self.confirmation_request is not None:
            raise ValueError("只有 needs_user 结果可以携带 confirmation_request")
        if self.status is ResultStatus.SUCCESS and self.retryable:
            raise ValueError("success 结果不能标记为 retryable")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at 不能早于 started_at")
        return self
```

实现时使用不可变 tuple 或 `Field(default_factory=list)`，不要使用可变默认值；`ActionType` 目标枚举必须包含 `tool_call`、`ask_user`、`final_answer`，但 M3 只启用三类动作的结构校验，`ask_user` 的暂停副作用留给 M5。当前仓库的 `ActionType` 尚无 `ASK_USER`，属于需要重构，不得把本示例当作已实现代码。`ToolResult` 的 `summary` 只能是受控、可序列化摘要；`result_ref` 指向完整结果或工具产物，`evidence_refs` 指向可追溯证据。`tool_call_id` 与 `ToolCall.action_id` 的值必须相同，前者只是 Tool Runtime 输出协议使用的字段名；`error_category/error_code/error_message/retryable` 是 `RunError` 的扁平线协议投影，不再维护另一套错误来源。

### 6.4 状态转换与恢复
新增纯函数 `transition_harness_state()` 和 `restore_harness_state()`，由 `LoopController` 唯一调用。M1 只冻结状态、身份和 checkpoint 恢复边界；确认回复的业务语义与 `confirmation_id` 对应关系留到暂停恢复模块。进程恢复命令 `RestoreRunCommand` 只允许恢复 `running` 或 `running/finalization`；用户确认恢复命令 `ResumeRunCommand` 只允许从 `waiting_confirmation` 进入 `running/restore_run`；终态一律不能通过普通恢复继续执行。两类恢复都必须校验 `user_id`、`conversation_id`、`thread_id`、`turn_id`、`run_id` 五个身份字段，并保留原 `original_goal`、`observations`、`plan_progress`。终态 Checkpoint 已写但收尾账本未完成时，不走普通恢复，改由 M6 的 `FinalizationService.reconcile()` 对账。

终态不能从业务阶段直接写入。必须先从任意 `running/*` 进入 `running/finalization`，并在 `terminal_intent` 中记录目标终态；收尾成功后才写入 `completed`、`failed`、`cancelled` 或 `timeout`。非法转换归类为 `ErrorCategory.CONFLICT`，不得静默覆盖 checkpoint。

### 6.5 第一阶段研发任务和验收
1. 重构 `app/agent/harness/contracts.py`：补齐 schema version、确认 DTO、状态快照、终态意图、非空/范围校验和 JSON 序列化测试。
2. 重构 `app/agent/harness/state.py`：控制状态默认值、合法转换、running/waiting_confirmation 恢复边界、完整身份校验和 checkpoint 版本边界。
3. 保持并校验 `app/agent/state.py`：保留已有可选 `harness` 字段、旧业务字段和 `messages` reducer，验证旧图节点仍可按原字段读写。
4. 修改 `app/services/agent_service.py`：明确新 run、进程 restore 与用户确认 resume 的边界；具体 Harness 入口接入留到 Loop Controller 模块。
5. 验收：新建、合法/非法转换、终态保护、身份保留、JSON 往返、版本校验和旧图回归测试通过；暂停确认与 action 幂等不在 M1 单独验收。

## 7. 统一 Harness 运行状态层

> 架构模块 1；本章小节沿用模块内编号，研发阶段编号见第 21 节。

> 本节是 M1 的最终设计基线。`app/agent/harness/` 当前仅有最小实现，以下契约优先于现有实验性代码；本节完成的是设计收口，不代表 M1 代码已经全部实现。

### 1.1 模块职责

**一句话职责**：在不替换现有 `AgentState` 的前提下，统一保存一次 Harness Run 的可恢复控制状态。

**需要重构**：

- 定义运行状态、循环阶段、重试计数、计划进度、工具观察、受控错误和 checkpoint schema version。
- 规定新建 run、进程中断恢复和后续确认恢复使用不同入口：进程恢复使用 `RestoreRunCommand(run_ref)`，用户确认恢复使用 `ResumeRunCommand(run_ref, reply)`；两者共享同一 `thread_id`、`turn_id` 和 `run_id`。
- 规定控制字段由 Loop Controller 唯一写入，旧业务节点继续读写原有扁平 `AgentState` 字段。
- 在 checkpoint 边界校验嵌套 `harness` 数据，并拒绝未知版本、非法状态和身份不匹配。

**需要新增**：

- 在现有 Harness 契约中新增 `ConfirmationStatus`、`ConfirmationVisibility`、`ConfirmationRecord`，并为缺失的 schema version、恢复引用和状态快照字段补齐明确 DTO 边界。

**不负责**：调用 LLM、选择工具、执行工具、构建 `CompiledContext`、保存会话最终结果、写长期记忆、实现 action 幂等或决定重试策略。M1 只冻结暂停相关 DTO 的结构，不实现暂停 API 和确认业务。

### 1.2 输入与输出

| 项目 | 类型 | 必填 | 来源/去向 | 校验与边界 |
| --- | --- | --- | --- | --- |
| 新建请求 | `HarnessRequest` | 是 | API/AgentService -> 状态初始化 | `mode=new` 时必须有非空 `input_text`，身份由服务层注入 |
| 进程恢复请求 | `HarnessRequest` | 是 | 内部恢复器/AgentService -> 状态恢复 | `mode=restore` 时归一化为 `RestoreRunCommand`；用户确认不使用此 DTO |
| 当前业务状态 | 现有 `AgentState` | 是 | `AgentService`/LangGraph Checkpointer | 保留原字段和 `messages` reducer，不整体替换 |
| 进程恢复命令 | `RestoreRunCommand` | 是 | M5 -> 状态恢复 | 只携带完整 `HarnessRunRef`，不携带用户回复 |
| 用户确认恢复命令 | `ResumeRunCommand` | 是 | M5 -> 状态恢复 | 必须携带 `HarnessRunRef` 和 `ConfirmationReply` |
| 控制状态 | `HarnessControlState` | 新建时生成，恢复时读取 | `state.harness` | 通过 `HarnessStateSnapshot` 校验 |
| 恢复引用 | `HarnessRunRef` | restore/resume 必填 | 命令 -> `restore_harness_state()` | 校验 `user_id`、`conversation_id`、`thread_id`、`turn_id`、`run_id` |
| 状态快照 | `HarnessStateSnapshot` | checkpoint 边界使用 | 状态层 -> `AsyncPostgresSaver` | JSON 可序列化；未知 schema version 拒绝 |

输出不是新的完整运行状态对象，而是写回现有 `AgentState` 的 `harness` 字段，并通过同一个 `AsyncPostgresSaver` 持久化。`PlannerStateView` 是给后续 Planning Agent 的受控投影，不得当作完整 checkpoint。

### 1.3 与现有 AgentState 的组合关系

**现有能力**：`app/agent/state.py::AgentState` 是 `TypedDict(total=False)`；业务字段保持扁平，`messages` 使用 `Annotated[list[AnyMessage], add_messages]`。当前工作树已有 `harness: HarnessControlState` 字段。

**需要重构**：不创建复制全部业务字段的 `HarnessRunState`。组合关系固定为：

```text
HarnessGraphState
├── AgentState 的既有身份、消息和业务字段
└── harness: HarnessControlState
```

身份字段唯一来源仍是 `AgentState`。当前已验证 `app/services/agent_service.py::_new_identity()` 固定 `thread_id == conversation_id`，新请求创建 `turn_id` 和 `run_id`；任何恢复命令都不得调用 `_new_identity()` 或 `_new_turn_state()` 清空现场。进程恢复使用 `RestoreRunCommand`；用户确认接口直接构造 `ResumeRunCommand`，不能让 Loop Controller 根据裸字典猜测恢复语义。

### 1.4 DTO 与 Protocol 契约

以下代码是设计语法，不是待执行代码。现有 `app/agent/harness/contracts.py` 中已有的基础枚举和 DTO 需要按此契约补齐；所有列表和字典使用 `Field(default_factory=...)`。

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 以下是跨文件设计片段；实际实现按 contracts.py、state.py 和 state 类型文件拆分。
from app.agent.harness.contracts import (
    ErrorCategory,
    HarnessStatus,
    LoopPhase,
    ResultStatus,
)
from app.agent.state import AgentState


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConfirmationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ConfirmationVisibility(StrEnum):
    """确认记录的发布可见性，不是确认业务状态。"""

    PREPARED = "prepared"
    PUBLISHED = "published"


class HarnessRunRef(ContractModel):
    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class HarnessRequest(ContractModel):
    """API/服务边缘的新建或进程恢复请求；确认恢复使用 ResumeRunCommand。"""

    mode: Literal["new", "restore"]
    input_text: str | None = Field(default=None, min_length=1)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    run_ref: HarnessRunRef | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "HarnessRequest":
        if self.mode == "new" and not self.input_text:
            raise ValueError("new run 必须提供 input_text")
        if self.mode == "restore" and self.run_ref is None:
            raise ValueError("restore 必须提供 run_ref")
        if self.mode == "restore" and (
            self.input_text is not None or self.project_id is not None or self.asset_ids
        ):
            raise ValueError("restore 不能覆盖原请求、项目或附件范围")
        return self


class PlanProgress(ContractModel):
    # 新 run 尚未完成首次规划时允许为空；original_goal 仍必须非空。
    goal_summary: str = Field(default="", max_length=2_000)
    completed_steps: list[str] = Field(default_factory=list, max_length=32)
    pending_steps: list[str] = Field(default_factory=list, max_length=32)
    blocked_reason: str | None = Field(default=None, max_length=1_000)


class RunError(ContractModel):
    category: ErrorCategory
    code: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False
    action_id: str | None = Field(default=None, min_length=1)


class RunObservation(ContractModel):
    observation_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    summary: str = Field(min_length=1, max_length=4_000)
    result_ref: str | None = Field(default=None, min_length=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)
    limitations: list[str] = Field(default_factory=list, max_length=32)
    output_hash: str | None = None


class ConfirmationRequest(ContractModel):
    confirmation_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    choices: list[str] = Field(default_factory=list, max_length=16)
    required_fields: tuple[str, ...] = Field(default=(), max_length=16)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_required_fields(self) -> "ConfirmationRequest":
        if any(not field.strip() for field in self.required_fields):
            raise ValueError("required_fields 不能包含空字段名")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields 不能重复")
        return self


class ConfirmationReply(ContractModel):
    confirmation_id: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4_000)
    decision: Literal["confirm", "reject"] = "confirm"
    resolved_conditions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_decision(self) -> "ConfirmationReply":
        if self.decision == "reject" and self.resolved_conditions:
            raise ValueError("reject 回复不能携带 resolved_conditions")
        return self


class ConfirmationRecord(ContractModel):
    """确认事实的持久化 DTO；业务状态和发布可见性分开表达。"""

    run_ref: HarnessRunRef
    request: ConfirmationRequest
    request_digest: str = Field(min_length=64, max_length=64)
    status: ConfirmationStatus = ConfirmationStatus.PENDING
    visibility: ConfirmationVisibility = ConfirmationVisibility.PREPARED
    reply: ConfirmationReply | None = None
    reply_digest: str | None = Field(default=None, min_length=64, max_length=64)
    prepared_at: datetime
    published_at: datetime | None = None
    resolved_at: datetime | None = None


class HarnessControlState(TypedDict, total=False):
    schema_version: int
    state_version: int
    fencing_token: int
    status: str
    phase: str
    iteration: int
    # 最近一次已提交正式动作的序号；新 run 初始为 0。
    action_seq: int
    max_iterations: int
    planner_retry_count: int
    context_retry_count: int
    tool_retry_counts: dict[str, int]
    last_context_build_id: str | None
    last_context_token_count: int | None
    started_at: datetime | None
    deadline_at: datetime | None
    cancel_requested: bool
    terminal_intent: str | None
    original_goal: str
    plan_progress: dict[str, Any]
    observations: list[dict[str, Any]]
    resolved_conditions: dict[str, Any]
    pending_confirmation: dict[str, Any] | None
    last_error: dict[str, Any] | None


class HarnessStateSnapshot(ContractModel):
    schema_version: int = Field(ge=1)
    state_version: int = Field(default=0, ge=0)
    fencing_token: int = Field(default=0, ge=0)
    status: HarnessStatus
    phase: LoopPhase
    iteration: int = Field(ge=0)
    # 新 run 初始为 0；正式动作提交后才从 1 开始递增。
    action_seq: int = Field(default=0, ge=0)
    max_iterations: int = Field(gt=0)
    planner_retry_count: int = Field(ge=0)
    context_retry_count: int = Field(ge=0)
    tool_retry_counts: dict[str, int] = Field(default_factory=dict)
    last_context_build_id: str | None = None
    last_context_token_count: int | None = Field(default=None, ge=0)
    started_at: datetime | None = None
    deadline_at: datetime | None = None
    cancel_requested: bool = False
    terminal_intent: Literal["completed", "failed", "cancelled", "timeout"] | None = None
    original_goal: str = Field(min_length=1)
    plan_progress: PlanProgress = Field(default_factory=PlanProgress)
    observations: list[RunObservation] = Field(default_factory=list)
    resolved_conditions: dict[str, Any] = Field(default_factory=dict)
    pending_confirmation: ConfirmationRequest | None = None
    last_error: RunError | None = None


class HarnessGraphState(AgentState, total=False):
    """现有 AgentState 加 Harness 控制字段的组合状态。"""

    project_id: str | None
    harness: HarnessControlState


class HarnessStateMachine(Protocol):
    def transition(
        self,
        state: HarnessControlState,
        *,
        status: HarnessStatus,
        phase: LoopPhase,
        terminal_intent: str | None = None,
    ) -> HarnessControlState: ...

    def restore(
        self,
        state: HarnessGraphState,
        ref: HarnessRunRef,
    ) -> HarnessGraphState: ...

    def validate_combination(
        self, state: HarnessControlState
    ) -> None:
        """校验 status/phase/terminal_intent 组合和逐阶段合法转换。"""


class CheckpointCodec(Protocol):
    def encode_harness(self, state: HarnessControlState) -> dict[str, Any]: ...
    def decode_harness(self, payload: dict[str, Any]) -> HarnessControlState: ...
```

`HarnessGraphState` 是对现有 `AgentState` 的目标组合类型说明，不是当前仓库已经导出的独立类型，也不是第二份运行时状态；M1 实现时应在现有状态边界中提供该组合类型，或使用等价的 TypedDict 组合。`project_id` 是需要新增到现有扁平业务状态的可选字段，不放入 `HarnessControlState`；当前 API 未提供时固定为 `None`。`CheckpointCodec` 只负责 `state.harness` 的校验和 JSON 编码，不负责调用或替代 `AsyncPostgresSaver`。`RunObservation` 与 M4 `ToolResult` 统一使用 `result_ref`、`evidence_refs` 和 `limitations`，完整结果不进入运行态正文。

### 1.5 状态、阶段与转换规则

当前 `app/agent/harness/contracts.py` 已有 `HarnessStatus`：`running`、`waiting_confirmation`、`completed`、`failed`、`cancelled`、`timeout`；已有 `LoopPhase`：`start_run`、`restore_run`、`build_context`、`plan`、`validate_action`、`execute_tool`、`handle_tool_result`、`record_observation`、`wait_confirmation`、`finalization`。后续模块不得另造同义枚举。`ConfirmationStatus` 需要新增，值为 `not_required`、`pending`、`confirmed`、`rejected`，仅表达确认业务状态；`ConfirmationVisibility` 的 `prepared/published` 只表达跨 Checkpointer 与业务表协调时的发布可见性，不能混入 `ConfirmationStatus`。状态机必须先调用 `validate_combination()` 校验当前组合，再校验目标组合和转换矩阵；不能只检查 HarnessStatus 是否变化。

合法状态组合只有以下几类：

| `status` | 合法 `phase` | `terminal_intent` | 说明 |
| --- | --- | --- | --- |
| `running` | `start_run/restore_run/build_context/plan/validate_action/execute_tool/handle_tool_result/record_observation` | 必须为空 | 普通运行阶段 |
| `running` | `finalization` | 必须为 `completed/failed/cancelled/timeout` 之一 | M6 正在执行或恢复收尾 |
| `waiting_confirmation` | 只能是 `wait_confirmation` | 必须为空 | 不得持有执行租约，不得调用 Planner/Tool/M6 |
| `completed` | 只能是 `finalization` | 必须为 `completed` | M6 主收尾完成 |
| `failed` | 只能是 `finalization` | 必须为 `failed` | M6 主收尾完成 |
| `cancelled` | 只能是 `finalization` | 必须为 `cancelled` | M6 主收尾完成 |
| `timeout` | 只能是 `finalization` | 必须为 `timeout` | M6 主收尾完成 |

逐阶段转换矩阵固定为：

| 当前组合 | 允许的下一组合 | 触发条件 |
| --- | --- | --- |
| absent | `running/start_run` | 新 run 身份、业务记录和初始状态已创建 |
| `running/start_run` | `running/build_context` 或 `running/finalization` | 初始化完成；初始化失败进入 failed intent |
| `running/restore_run` | 原安全阶段、`running/build_context` 或 `running/finalization` | 先完成 checkpoint/业务记录/动作/工具结果对账；确认恢复固定进入 build_context |
| `running/build_context` | 同阶段、`running/plan` 或 `running/finalization` | 同请求有限重试、成功、重试耗尽/取消/超时 |
| `running/plan` | 同阶段、`running/validate_action` 或 `running/finalization` | Planner 有限重试、合法动作、不可恢复失败/取消/超时 |
| `running/validate_action` | 同阶段、`running/execute_tool`、`waiting_confirmation/wait_confirmation` 或 `running/finalization` | 动作协调提交、三类动作分派或失败 |
| `running/execute_tool` | 同阶段、`running/handle_tool_result` 或 `running/finalization` | 同 action attempt 重试、获得确定结果、取消/超时 |
| `running/handle_tool_result` | `running/record_observation`、`waiting_confirmation/wait_confirmation` 或 `running/finalization` | 普通结果、needs_user、不可补偿失败 |
| `running/record_observation` | `running/build_context` 或 `running/finalization` | 继续下一轮或终止条件成立 |
| `waiting_confirmation/wait_confirmation` | `running/restore_run` 或 `running/finalization` | 确认继续；拒绝/取消进入 cancelled intent |
| `running/finalization` | 同组合或对应 terminal/finalization | M6 重试/恢复；主收尾完成 |
| terminal/finalization | 相同 terminal/finalization | 只允许幂等读取/对账，禁止恢复业务循环 |

“原安全阶段”不是任意跳转：进程 restore 必须根据已持久化 phase 及其业务事实选择该阶段专属恢复器。例如 validate_action 先对账 prepared 动作，execute_tool 先查 committed 动作和 execution record，finalization 只进入 M6。不得仅把 phase 改回原值后直接重放副作用。

终态不能从业务阶段直接写入。必须先进入 `running/finalization`，由 `terminal_intent` 记录目标终态；只有 Finalization 成功后才落到 terminal status。收尾失败时保持 `running/finalization`，不得误报 `completed`。`waiting_confirmation` 不得直接转换为 terminal；拒绝或取消必须先取得执行租约并进入 `running/finalization + terminal_intent=cancelled`。`waiting_confirmation` 不得调用 Planner 或 Tool Runtime；终态不得继续执行动作；计数不得为负；`original_goal` 不得在同一 run 中覆盖；同一终态重复提交只能返回幂等成功。

### 1.6 初始化、更新、暂停和恢复

```text
start_new_run(request):
    assert request.input_text is not None
    identity = AgentService._new_identity(request.conversation_id)
    state = AgentService._new_turn_state()
    state["input_text"] = request.input_text
    state["project_id"] = request.project_id
    state["asset_ids"] = list(request.asset_ids)
    state["user_id"], state["conversation_id"], state["thread_id"], \
    state["turn_id"], state["run_id"] = identity
    state["harness"] = new_harness_control_state(
        schema_version=current_version,
        status=running, phase=start_run, original_goal=request.input_text,
        started_at=utc_now(), deadline_at=utc_now() + configured_run_timeout,
        cancel_requested=False,
    )
    state["messages"] = [
        HumanMessage(
            id=f"turn:{identity.turn_id}:user",
            content=request.input_text,
            additional_kwargs={
                "turn_id": identity.turn_id,
                "run_id": identity.run_id,
                "asset_ids": list(request.asset_ids),
            },
        )
    ]
    persist through the existing graph and AsyncPostgresSaver
    return state

update_control_state(state, patch):
    validate patch ownership and JSON-safe values
    apply only through state transition/update functions
    persist checkpoint before invoking the next external side effect
    return state

restore_process(command: RestoreRunCommand):
    ref = command.run_ref
    assert ref is not None
    state = load checkpoint by ref.thread_id
    verify state["user_id"] == ref.user_id
    verify state["conversation_id"] == ref.conversation_id
    verify state["thread_id"] == ref.thread_id
    verify state["turn_id"] == ref.turn_id
    verify state["run_id"] == ref.run_id
    harness = state["harness"]
    verify harness["schema_version"] is supported
    reject terminal status and waiting_confirmation; allow running or running/finalization
    preserve original_goal, plan_progress, observations and business fields
    persist through the existing AsyncPostgresSaver
    return state

encode_harness(state):
    snapshot = HarnessStateSnapshot.model_validate(state["harness"])
    return snapshot.model_dump(mode="json")
```

M1 不把进程恢复限定为某一个业务阶段：进程可能在任意业务阶段或 `finalization` 阶段中断。`RestoreRunCommand` 只处理这类无用户回复的进程恢复；`waiting_confirmation` 必须由 M5 的 `ResumeRunCommand` 校验确认 ID、过期时间和回答语义后再转为 `running/restore_run`。M1 只要求保留 `pending_confirmation` 的 JSON 边界。

### 1.7 Checkpointer 边界与旧图兼容

**现有能力**：`app/clients/postgres_client.py` 已使用 `AsyncPostgresSaver.from_conn_string()`、`await checkpointer.setup()` 并将 Saver 传入 `build_agent_graph(checkpointer=checkpointer)`。

**设计约束**：

- `AsyncPostgresSaver` 继续保存完整 LangGraph 状态；不新增第二套 Checkpointer 表。
- `HarnessStateSnapshot` 只校验嵌套 `state.harness`，不保存 `CompiledContext`、完整工具结果、完整事件流、附件正文、SQL rows、Python 源码、数据库 session 或异常对象。
- 旧固定图继续读取扁平字段；M1 不删除 `build_agent_graph()`，也不要求旧业务节点理解 Harness 控制字段。
- 过渡期间必须明确同一个 `thread_id` 的唯一写入图；在默认入口切换前，不允许旧图和 Harness 图并发写同一 run。

### 1.8 文件级任务、测试与验收

| 任务 | 类型 | 文件 | 产出 | 前置 |
| --- | --- | --- | --- | --- |
| M1.1 | 需要重构 | `app/agent/harness/contracts.py` | 补齐请求、状态快照、计划、观察、错误、`ConfirmationStatus`、`ConfirmationVisibility` 和 `ConfirmationRecord` | 无 |
| M1.2 | 需要重构 | `app/agent/harness/state.py` | 默认状态、状态/阶段校验、finalization 中间态、完整身份恢复和版本边界 | M1.1 |
| M1.3 | 需要重构 | `app/agent/state.py` | 保留旧字段和 reducer，声明可选 `project_id` 与 `harness` 组合关系 | M1.1/M1.2 |
| M1.4 | 需要重构 | `app/services/agent_service.py` | 分离新建与恢复，不让 resume 清空 turn 现场 | M1.2 |
| M1.5 | 需要新增 | `tests/test_harness_state.py` | DTO、默认容器、转换、身份、版本、checkpoint 和旧图兼容测试 | M1.1~M1.4 |

M1 验收标准：

1. 所有 DTO 的空值、范围、枚举、额外字段和 JSON 序列化规则有测试；默认列表/字典不跨 run 共享。
2. 业务阶段不能直接进入 terminal status；所有 terminal intent 先经过 `running/finalization`。
3. `RestoreRunCommand` 校验五个身份字段，并只允许恢复 `running` 或 `running/finalization`；`ResumeRunCommand` 只允许消费 `waiting_confirmation`；终态拒绝普通恢复。
4. 两类恢复均保留原 `turn_id`、`run_id`、`thread_id`、目标、观察、计划进度和旧业务字段，不调用新 run 初始化逻辑；终态收尾对账由 M6 单独处理。
5. `messages` reducer 和旧固定图构建行为不变；状态层不调用 LLM、ContextEngine、Tool Runtime、ConversationRepository 或 MemoryManager。
6. M1 设计完成不等于 M1 代码完成；实现和测试通过后，才允许进入 M2 的代码接入，但本文可以继续记录 M2 设计。

**完善判定**：M1 已覆盖统一状态落点、DTO、状态转换、终态保护、身份恢复、Checkpointer 边界、旧图兼容、文件任务、测试和进入 M2 的门槛，文档层面判定为完善；代码层面仍属于需要重构/新增，必须完成 M1.1～M1.5 并通过测试后才算实现完成。

## 8. ContextEngine 集成

> 架构模块 2；本章小节沿用模块内编号，研发阶段编号见第 21 节。

> 本节是 M2 的文档设计基线。M2 只完成 Harness 到现有 ContextEngine 的集成契约，不实现 Planning Agent、Tool Runtime、Loop Controller 或暂停 API。

### 2.1 模块职责

**一句话职责**：把 M1 的 Harness 运行状态投影为现有 `ContextRequest`，直接调用现有 `ContextEngine.build()`，生成供一次 Planning Agent 调用使用的唯一 `CompiledContext`。

**现有能力**：

- `app/agent/context_engine/engine.py::ContextEngine.build()` 已实现异步上下文构建入口。
- `app/agent/context_engine/contracts.py` 已提供 `ContextRequest`、`CompiledContext`、`ContextSections`、`ContextBuildTrace`。
- `app/agent/context_engine/history.py` 已提供 Working 窗口和持久化增量摘要。
- `app/agent/context_engine/resolver.py`、`planner.py`、`selector.py`、`deduplicator.py` 已提供引用解析、召回规划、候选选择和去重。

**需要重构**：

- 扩展现有 `ContextRequest`，增加可选 `runtime_context`，不创建第二套请求 DTO 或第二套 `CompiledContext`。
- 将 Harness 控制状态转换为只读、有限大小的 `RuntimeContext`，由现有 `ContextCompiler` 编译为受控背景消息。
- 在 Harness 的 `build_context` 阶段依赖注入现有 `ContextEngine`，成功后仅回写 `build_id` 和 token 统计。
- 统一 M2 的预算、身份、权限和错误边界，确保运行态数据不会绕过 Loop Controller 修改状态。

**需要新增**：

- 新增中性的 `RuntimeContext` 及其子 DTO，并新增只负责状态投影和请求构造的 `context_service.py`。

**不负责**：选择下一步工具；执行 SQL、Python 或报告渲染；修改完整 `AgentState`；决定重试、暂停、取消或终止；写入长期记忆；保存完整工具结果；替代 Checkpointer。

### 2.2 已验证基线与增量结论

`app/agent/context_engine/engine.py` 的真实入口为：

```python
async def build(self, request: ContextRequest) -> CompiledContext
```

`ContextRequest` 当前位于 `app/agent/context_engine/contracts.py`，是不可变 dataclass，已有字段：

- `user_id: str`
- `conversation_id: str`
- `query: str`
- `system_instructions: str`
- `agent_type: str = "general"`
- `project_id: str | None = None`
- `asset_ids: tuple[str, ...] = ()`
- `memory_types: tuple[MemoryType, ...] | None = None`
- `enable_rag: bool = False`
- `token_budget: int | None = None`

`CompiledContext` 当前字段为 `build_id`、`messages`、`sections`、`token_count`、`resolved_asset_ids`、`trace`。它是一次模型调用的只读上下文快照，不是完整运行状态、计划对象、工具结果仓库或长期记忆。

当前 `ContextCompiler.compile()` 只根据 `selected` 和 `ReferenceResolution` 生成现有背景消息，`base_token_count()` 只计算 `system_instructions` 与当前 query；现有代码不会读取 `ContextRequest.runtime_context`。因此“运行态背景块”和“运行态纳入基础 token 预算”均属于 M2 的需要重构，不是现有能力。

现有构建流程已验证为：

```text
validate request
    -> start_build
    -> load_working
    -> resolve references
    -> plan retrieval
    -> prepare history/summary
    -> gather memory/assets/RAG
    -> deduplicate
    -> select/compress by token budget
    -> enrich source references
    -> compile
    -> finish_build
```

`app/agent/context_engine/factory.py::build_context_engine()` 已完成独立依赖组装，并明确不会自动接入 Agent 图。M2 的唯一集成结论是：不创建 `ContextEngineAdapter`；新增一个纯状态投影服务即可，生产调用仍直接使用 `ContextEngine.build()`。

**现有测试基线**：`tests/test_context_engine.py` 已覆盖 `ContextReferenceResolver` 的历史/附件解析、摘要增量更新、候选去重与选择、明确附件注入、最终消息 token 预算、`ContextBuildTrace` 的正文隔离和 ContextStore 完成审计。当前没有 `runtime_context` 兼容性、Harness 状态投影、运行态消息预算或显式附件越权映射测试；这些属于 M2 必须新增的验证面。

### 2.3 输入 DTO 与校验

M2 的目标上游是 M1 的 `HarnessGraphState`、应用级 Agent 配置和已注入的 `ContextEngine`。当前仓库实际只有 `app/agent/state.py::AgentState`，因此在 M1 完成组合类型之前，M2 不得直接新增一个平行状态对象或假设该符号已经可导入。输入字段如下：

| 字段 | 类型 | 必填 | 来源 | 校验与边界 |
| --- | --- | --- | --- | --- |
| `user_id` | `str` | 是 | `HarnessGraphState.user_id` | 非空；只能由服务层提供，`runtime_context` 不能覆盖 |
| `conversation_id` | `str` | 是 | `HarnessGraphState.conversation_id` | 非空；与 Working loader 使用的会话/thread 映射一致 |
| `query` | `str` | 是 | `HarnessGraphState.input_text` | 非空；保留用户原问题，不能被工具摘要替换 |
| `system_instructions` | `str` | 是 | Agent 配置 | 只放系统行为约束，不拼接运行态 JSON |
| `agent_type` | `str` | 否 | Agent 配置 | 为空使用现有默认值 `general` |
| `project_id` | `str | None` | 否 | `HarnessGraphState.project_id` | 只用于现有项目范围检索；恢复时沿用原值 |
| `asset_ids` | `tuple[str, ...]` | 否 | `HarnessGraphState.asset_ids` | 沿用现有最多 32 个 ID 和 ID 长度校验 |
| `memory_types` | `tuple[MemoryType, ...] \| None` | 否 | Agent 策略 | 沿用现有长期记忆筛选规则 |
| `enable_rag` | `bool` | 否 | Agent 策略 | 只有配置允许时才可启用现有 RAG 端口 |
| `token_budget` | `int | None` | 否 | Agent 配置 | 为空使用 `ContextPolicy.max_context_tokens`，否则必须大于 0 |
| `runtime_context` | `RuntimeContext | None` | 否 | `HarnessControlState` 的只读投影 | 只能含受控摘要、已确认条件、引用和哈希 |

新增运行态 DTO 使用 Pydantic；以下为设计契约，不是实现代码：

```python
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


RuntimeKey = Annotated[str, Field(min_length=1, max_length=128)]
RuntimeValue = Annotated[str, Field(min_length=1, max_length=1_000)]
PlanStep = Annotated[str, Field(min_length=1, max_length=256)]
ArtifactRef = Annotated[str, Field(min_length=1, max_length=256)]
Digest = Annotated[str, Field(min_length=1, max_length=128)]


class RuntimeCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: RuntimeKey
    value: RuntimeValue


class RuntimeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: RuntimeKey
    tool_name: RuntimeKey
    status: Literal[
        "success",
        "partial",
        "temporary_error",
        "needs_user",
        "unrecoverable_error",
    ]
    summary: RuntimeValue
    result_ref: ArtifactRef | None = None
    evidence_refs: tuple[ArtifactRef, ...] = Field(
        default_factory=tuple, max_length=32
    )
    limitations: tuple[RuntimeValue, ...] = Field(
        default_factory=tuple, max_length=16
    )
    output_hash: Digest | None = None


class RuntimeErrorSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: Literal[
        "validation",
        "planner",
        "context",
        "tool",
        "database",
        "timeout",
        "permission",
        "user_input",
        "conflict",
        "cancelled",
        "unknown",
    ]
    code: RuntimeKey
    message: RuntimeValue
    retryable: bool = False


class RuntimePlanProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    goal_summary: str = Field(default="", max_length=2_000)
    completed_steps: tuple[PlanStep, ...] = Field(
        default_factory=tuple, max_length=32
    )
    pending_steps: tuple[PlanStep, ...] = Field(
        default_factory=tuple, max_length=32
    )
    blocked_reason: RuntimeValue | None = None


class RuntimeContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    original_goal: str = Field(min_length=1, max_length=8_000)
    # 值已由投影层规范化为短字符串；不得把任意嵌套对象直接传入 Prompt。
    resolved_conditions: tuple[RuntimeCondition, ...] = Field(
        default_factory=tuple, max_length=16
    )
    plan_progress: RuntimePlanProgress = Field(default_factory=RuntimePlanProgress)
    observations: tuple[RuntimeObservation, ...] = Field(
        default_factory=tuple, max_length=12
    )
    recent_errors: tuple[RuntimeErrorSummary, ...] = Field(
        default_factory=tuple, max_length=4
    )

```

`RuntimeContext` 及其四个子 DTO 放在 `app/agent/harness/context_contracts.py`，作为不携带 LangGraph 状态和 Harness 枚举的中性投影契约；`app/agent/harness/context_service.py` 只负责从 Harness 状态生成该 DTO。`app/agent/context_engine/contracts.py::ContextRequest` 只增加 `runtime_context: RuntimeContext | None = None` 并复用该 DTO。依赖方向固定为 Harness 状态 -> 中性运行态 DTO -> ContextEngine；ContextEngine 不导入 Harness 状态或 Harness 枚举。`resolved_conditions` 的值必须在投影层转成有长度上限的 `RuntimeCondition`；未确认或无法安全压缩的值不得进入本 DTO。

`RuntimeContext` 不包含 `user_id`、`conversation_id`、`thread_id`、`turn_id` 或 `run_id`。这些身份字段只能来自 `ContextRequest` 和 M1 的 `AgentState`，运行态投影不得覆盖身份边界；这也是避免工具摘要伪造访问范围的必要条件。

现有 `ContextRequest` 仍保持 dataclass 兼容，只增加一个末尾可选字段：

```python
from dataclasses import dataclass

from app.agent.harness.context_contracts import RuntimeContext
from app.agent.memory.enums import MemoryType


@dataclass(frozen=True, slots=True)
class ContextRequest:
    user_id: str
    conversation_id: str
    query: str
    system_instructions: str
    agent_type: str = "general"
    project_id: str | None = None
    asset_ids: tuple[str, ...] = ()
    memory_types: tuple[MemoryType, ...] | None = None
    enable_rag: bool = False
    token_budget: int | None = None
    runtime_context: RuntimeContext | None = None
```

这是对现有 DTO 的兼容扩展，不是新建 `ContextRequestWithRuntime`。旧调用不传 `runtime_context` 时，默认行为、消息顺序和 `CompiledContext` 字段必须保持不变。

### 2.4 输出 DTO 与存储边界

输出仍然只有现有 `CompiledContext`：

| 输出字段 | 类型 | 下游 | Checkpointer | PostgreSQL | 说明 |
| --- | --- | --- | --- | --- | --- |
| `build_id` | `str` | Harness 状态、Planner 观测 | 只保存为 `last_context_build_id` | `context_build_runs.build_id` | 不保存完整上下文正文 |
| `messages` | `tuple[dict[str, Any], ...]` | `PlannerInput.compiled_context.messages` | 不保存 | 不直接保存正文 | 一次规划的临时模型输入 |
| `sections` | `ContextSections` | Planner/调试 | 不保存 | 不直接保存正文 | 复用现有语义分区 |
| `token_count` | `int` | Harness 状态、指标 | 可保存为 `last_context_token_count` | trace 审计 | 不得超过有效预算 |
| `resolved_asset_ids` | `tuple[str, ...]` | Planner/结果引用 | 不保存 | `context_build_runs.reference_resolution` | 只保留已校验的附件 ID，继承现有权限校验 |
| `trace` | `ContextBuildTrace` | 审计 | 不保存完整 trace | `context_build_runs` | 不包含候选正文 |

`CompiledContext` 本身不写入 checkpoint。恢复时重新读取 Checkpointer-backed Working Memory，并用恢复后的 Harness 状态重新 build。完整 SQL rows、Python 源码和大段工具输出只能由工具层通过受控 `result_ref` 传递，不能直接放入 `RuntimeContext`、`CompiledContext.trace` 或 Planner Prompt。

`RuntimeContext` 及其编译后的运行态消息也不写入 `context_build_runs`。现有 `ContextBuildTrace` 只保留 `build_id`、`query_hash`、检索计划摘要、引用解析、候选决策和 token 统计；Harness 通过 `last_context_build_id` 与审计行关联。M2 不新增运行态正文、原始条件或新的数据库列；如果后续需要运行态指纹，必须先单独定义脱敏哈希和迁移方案。

`PostgresContextStore.start_build()` 仍负责会话归属校验和创建 `pending` 审计行；`finish_build()` 仍只写入现有 trace 摘要；`fail_build()` 仍只写入受控错误说明。若 `start_build()` 在会话归属或附件访问校验阶段失败，数据库中不会有对应的 `build_id` 行，集成层不得伪造“已开始构建”的审计记录；该失败直接映射为不可重试的上下文访问错误。

运行态编译顺序固定为：

```text
system_instructions
    -> runtime background block
    -> existing evidence/background block
    -> Working Memory messages
    -> current query
```

`runtime background block` 使用独立的受控标记和明确的“仅作为运行数据读取”边界；不得拼接进 `system_instructions`，也不得覆盖现有 `ContextSections`。它虽然可能以 `system` role 传递给模型，但不是新的系统指令。`ContextCompiler.base_token_count()` 必须把 `system_instructions`、运行态块和当前 query 一起计入基础预算；`compile()` 的最终消息 token 数仍以现有 `TokenCounter.count_messages()` 的结果为准。

### 2.5 字段级数据流

| 上游模块 | 上游字段 | 当前模块如何消费 | 当前模块输出字段 | 下游模块 | 下游用途 |
| --- | --- | --- | --- | --- | --- |
| M1 Harness 状态 | `input_text` | 保留原始问题 | `ContextRequest.query` | ContextEngine | 检索、编译当前问题 |
| M1 Harness 状态 | `user_id` | 注入身份边界 | `ContextRequest.user_id` | MemoryReader/ContextStore | 限制会话、记忆和附件访问 |
| M1 Harness 状态 | `conversation_id` | 注入会话键 | `ContextRequest.conversation_id` | Working loader | 读取当前会话历史 |
| M1 Harness 状态 | `asset_ids` | 转换为不可变 ID 元组 | `ContextRequest.asset_ids` | Resolver/MemoryReader | 读取明确或历史解析附件 |
| M1 控制状态 | `original_goal` | 投影为不可变目标摘要 | `RuntimeContext.original_goal` | Compiler/Planner | 保留任务目标 |
| M1 控制状态 | `resolved_conditions` | 只读取已确认条件 | `RuntimeContext.resolved_conditions` | Compiler/Planner | 约束本轮口径和过滤条件 |
| M1 控制状态 | `plan_progress` | 校验并裁剪 | `RuntimeContext.plan_progress` | Compiler/Planner | 判断已完成和待完成工作 |
| M1 控制状态 | `observations` | 取最新摘要、引用、哈希 | `RuntimeContext.observations` | Compiler/Planner | 使用工具产生的新证据 |
| M1 控制状态 | `last_error` | 转为脱敏错误摘要 | `RuntimeContext.recent_errors` | Compiler/Planner | 识别限制，不泄露堆栈 |
| `RuntimeContext` | 五类运行态摘要 | 编译为受控背景消息 | `CompiledContext.messages` | Planning Agent | 读取最新运行现场 |
| ContextEngine | `build()` 返回值 | 保存引用和统计，不保存正文 | `build_id`、`token_count`、trace 摘要 | Loop Controller/审计 | 规划前观测和后续状态 |
| Tool Runtime | `ToolResult.result_ref`、`ToolResult.evidence_refs` | 映射为受控结果和证据引用 | `RuntimeObservation.result_ref`、`RuntimeObservation.evidence_refs` | 下一次 build | 通过引用读取结果，不复制结果 |
| `CompiledContext.messages` | 标准 role/content 消息 | 只读传入规划器 | `PlannerInput.compiled_context.messages` | Planning Agent | 生成下一步动作 |

当前代码没有独立的工具结果 ArtifactStore；`result_ref`、`evidence_refs` 是 M4 统一 DTO 和持久化边界需要新增的字段，不代表现有工具已经返回这些引用。

`RuntimeContext` 不等于新的召回来源，也不替代 `ContextRetrievalPlan`。M2 默认只把运行态编译为补充背景；是否召回 Working、Semantic、Episodic、Perceptual 或 RAG 仍由现有 `ContextRequest` 策略、`ContextReferenceResolver` 和 `ContextPlanner` 决定。运行态中的工具摘要不得被当作长期记忆事实自动写回 Memory，也不得改变 `user_id`、`conversation_id`、`asset_ids` 的访问边界。

### 2.6 与现有代码的衔接点

**现有能力，直接复用**：

- `app/agent/context_engine/engine.py::ContextEngine.build()`：唯一生产入口。
- `app/agent/context_engine/contracts.py::ContextRequest`、`CompiledContext`、`ContextSections`、`ContextBuildTrace`：维持稳定契约。
- `app/agent/context_engine/compiler.py::ContextCompiler.compile()`、`base_token_count()`、`_supplemental_message()`：增加运行态消息编译和基础预算计数。
- `app/agent/context_engine/history.py::ConversationHistoryManager`：维持 Working 窗口与持久化摘要。
- `app/agent/context_engine/resolver.py`、`planner.py`、`selector.py`、`deduplicator.py`：维持引用解析、召回、候选选择和去重。
- `app/agent/context_engine/factory.py::build_context_engine()`：维持依赖注入，不在模块内部创建外部客户端。
- `app/agent/memory/interfaces.py::MemoryContextReader`：维持记忆只读边界；Working Memory 仍来自 Checkpointer-backed loader。
- `app/repositories/context_repository.py::PostgresContextStore.start_build()`、`finish_build()`、`fail_build()`、`_ensure_conversation_access()`：维持摘要和 `context_build_runs` 审计及会话权限边界。

**需要重构**：

- `app/agent/context_engine/contracts.py`：在现有 `ContextRequest` 末尾增加 `runtime_context`，不改变已有字段默认值和位置语义。
- `app/agent/context_engine/compiler.py`：在 supplemental background 区域增加明确的运行态数据块；不得把运行态拼到 `system_instructions`。
- `app/agent/context_engine/engine.py`：让基础 token 计算包含系统指令、运行态块和当前 query；保留现有 `fail_build()`、日志和原异常重抛行为。
- `app/agent/state.py`、`app/agent/harness/state.py`：由 M1 提供受控状态和唯一写入规则；ContextEngine 不直接修改它们。

**需要新增**：

- `app/agent/harness/context_service.py`：把状态投影为 ContextEngine 已定义的 `RuntimeContext`，并构造 `ContextRequest`；不命名为 `ContextEngineAdapter`，不执行检索。
- `tests/test_harness_context.py`：字段映射、裁剪、身份隔离、恢复重建和大对象排除测试。
- `tests/test_context_engine.py`：`runtime_context=None` 的旧行为回归、运行态消息和 token 预算测试。

**暂不启用**：把 `RuntimeContext` 作为独立记忆类型、把运行态摘要自动写入长期 Memory；结果和证据引用由 M4 统一定义，但在 M4 ArtifactStore 未完成前不能声称已经可持久化。

**暂不修改/暂不删除**：旧固定图、`build_agent_graph()`、Memory Formation、Query/Analysis/Report 节点和现有普通 Agent API；统一入口留到后续模块。保留现有 `build_context_engine()` 作为应用级装配入口，以及不传 `runtime_context` 的所有旧 `ContextRequest` 调用。M2 不删除旧代码，也不创建 `ContextEngineAdapter`。

**待验证**：LangGraph Checkpointer 对嵌套 `harness` 状态的实际 JSON 往返；运行态背景块在目标 ChatModel 中的消息角色和 token 计数；显式附件越权是否需要由 Resolver 抛错还是由 Harness 集成层统一升级；同一会话恢复时 Working loader 与 `conversation_id == thread_id` 的一致性。

### 2.7 接口契约

以下接口使用 Protocol；接口只表达边界，不实现外部调用：

```python
from typing import Any, Protocol

from pydantic import Field, model_validator

from app.agent.context_engine.contracts import CompiledContext, ContextRequest
from app.agent.harness.state import HarnessControlState
from app.agent.memory.enums import MemoryType
from app.agent.harness.context_contracts import RuntimeContext
from app.agent.harness.contracts import HarnessGraphState

# 复用 M1 的唯一 HarnessGraphState 定义；M2 不重复声明状态类型。


class RuntimeContextProjector(Protocol):
    def project(self, state: HarnessGraphState) -> RuntimeContext: ...


class ContextBuilder(Protocol):
    async def build(self, request: ContextRequest) -> CompiledContext: ...

class ContextRequestFactory(Protocol):
    def create(
        self,
        state: HarnessGraphState,
        *,
        system_instructions: str,
        agent_type: str = "general",
        memory_types: tuple[MemoryType, ...] | None = None,
        enable_rag: bool = False,
        token_budget: int | None = None,
    ) -> ContextRequest: ...
```

`ContextBuilder` 仅用于单元测试替换现有 `ContextEngine`；生产对象仍是 `ContextEngine`。`ContextRequestFactory` 只做字段映射和 DTO 校验，不创建 PostgreSQL、Qdrant、Neo4j、RAG 或 LLM 客户端。`project_id` 必须固定从 `state.get("project_id")` 读取，工厂不提供可覆盖状态的同名参数；这样恢复和最终收尾使用同一项目边界。构造错误归类为 `validation`；`ContextEngine.build()` 失败由 Harness 集成层转换为 `RunError(category=ErrorCategory.CONTEXT, ...)`，ContextEngine 本身不依赖 Harness 错误枚举。

### 2.8 核心伪代码

```text
project_runtime(harness_state):
    snapshot = HarnessStateSnapshot.model_validate(harness_state)
    runtime = RuntimeContext(
        original_goal=snapshot.original_goal,
        resolved_conditions=tuple(
            RuntimeCondition(key=key, value=compact_string(value))
            for key, value in only_confirmed_conditions(
                snapshot.resolved_conditions
            ).items()
        ),
        plan_progress=compact_plan_progress(snapshot.plan_progress),
        observations=latest_observations(
            snapshot.observations, limit=12
        ),
        recent_errors=latest_errors(
            snapshot.last_error, limit=4
        ),
    )
    return runtime

build_context(state, config):
    runtime = project_runtime(state["harness"])
    request = ContextRequest(
        user_id=state["user_id"],
        conversation_id=state["conversation_id"],
        query=state["input_text"],
        system_instructions=config.system_instructions,
        agent_type=config.agent_type,
        project_id=state.get("project_id"),
        asset_ids=tuple(state.get("asset_ids", ())),
        memory_types=config.memory_types,
        enable_rag=config.enable_rag,
        token_budget=config.token_budget,
        runtime_context=runtime,
    )
    try:
        compiled = await context_engine.build(request)
    except PermissionError as exc:
        raise classified_context_error(
            category="permission", code="context_access_denied", cause=exc
        ) from exc
    except ValueError as exc:
        raise classified_context_error(
            category="validation", code="context_request_invalid", cause=exc
        ) from exc
    except Exception as exc:
        raise classified_context_error(
            category="context", code="context_build_failed", cause=exc
        ) from exc
    # Loop Controller 通过 M1 状态转换函数记录 build_id 和 token_count。
    return compiled
```

伪代码只描述本模块的状态投影、请求构造和 ContextEngine 调用。成功路径只返回现有 `CompiledContext`；失败路径抛出集成层受控异常，由 Loop Controller 映射为 M1 `RunError`，不能返回一个与 `ContextBuilder` 契约不一致的错误 DTO。`classified_context_error()` 只是错误分类伪函数，不是当前仓库已有接口。`build_id` 和 token 统计由 Loop Controller 通过 M1 状态转换函数记录，ContextEngine 不直接修改 `HarnessControlState`。状态转换、重试、暂停、恢复和下一步调度由后续 Loop Controller 负责。工具结果不是最终答案，而是下一次规划的新证据；`success` 或 `partial` 结果写入 M1 `RunObservation` 后，必须重新执行 `build_context()`。`needs_user` 结果先回交暂停流程，恢复后再 build。

### 2.9 重建、预算与错误边界

| 事件 | 是否重新 build | 原因 |
| --- | --- | --- |
| 新建 run 进入首次规划 | 是 | 需要生成首个最新上下文快照 |
| 工具返回 `success` 或 `partial` 并记录观察 | 是 | 工具结果成为下一次规划的新证据 |
| 同一临时错误仍可安全重试 | 否 | 没有新增可规划事实，避免无意义构建 |
| 临时错误耗尽并准备重新规划 | 是 | 错误摘要已写入运行态 |
| 用户确认恢复 | 是 | `resolved_conditions` 发生变化 |
| Planner 仅输出格式错误并重试 | 否 | 只修复动作格式，不改变上下文事实 |
| ContextEngine 同一请求因可重试依赖失败 | 否 | 由 Loop Controller 重试同一请求并计数；校验、会话归属和附件访问错误不得重试 |
| 进入 `running/finalization` | 否 | Finalization 不再需要规划上下文 |

预算规则：

1. 有效预算来自 `request.token_budget` 或现有 `ContextPolicy.max_context_tokens`。
2. 系统指令、运行态背景消息和当前 query 均属于基础输入，必须在候选召回前计数；基础输入超预算时不使用空上下文继续调用 Planner。
3. 运行态投影先按“原始目标 -> 已确认条件 -> 计划摘要 -> 最新观察 -> 最新错误”保留和裁剪；完整结果只保留 `result_ref`，证据链只保留 `evidence_refs`。
4. 现有候选仍由 `ContextSelector` 和 `TokenBoundaryCompressor` 按已有策略选择和压缩；M2 不复制一套 selector。
5. `runtime_context=None` 时不得增加运行态背景消息，确保现有 ContextEngine 测试和普通调用行为不变。

错误边界：

- `ContextEngine._validate_request()` 当前在生成 `build_id` 和调用 `start_build()` 之前对空身份、空 query、非法预算和非法附件 ID 抛出 `ValueError`；这类失败当前没有 `context_build_runs` 审计行，M2 将其映射为 `validation`，不可重试。
- `PostgresContextStore._ensure_conversation_access()` 当前在 `start_build()` 阶段抛出 `PermissionError`；它发生在现有 `ContextEngine.build()` 的主 `try` 之前，M2 必须把建构启动和失败分类纳入同一集成边界，且不得把该错误重试为上下文构建。
- 当前附件解析器会把不存在或无权访问的附件放入 `unresolved_references` 并继续构建。M2 必须区分“请求显式提供的 `asset_ids`”与“历史自然语言引用”：前者的无权访问应转换为不可重试的 `permission` 错误，后者仍可保留为受控未解析引用并交给后续澄清流程。
- 临时记忆、附件或 RAG 依赖不可用，可由 Loop Controller 按配置重试；ContextEngine 不自行重试。
- 当前 `ContextEngine.build()` 已记录应用日志、调用 `ContextStore.fail_build()` 并重新抛出原异常；M2 保留该行为。
- 集成层只保存 `ErrorCategory`、稳定错误码、脱敏消息和 retryable 标志，不保存异常对象、完整堆栈或原始 prompt。

### 2.10 文件级任务与测试验收

| 任务 | 类型 | 文件 | 产出 | 前置 |
| --- | --- | --- | --- | --- |
| M2.1a | 需要新增 | `app/agent/harness/context_contracts.py` | 新增 `RuntimeContext`、`RuntimeCondition`、`RuntimeObservation`、`RuntimeErrorSummary`、`RuntimePlanProgress` | M1 契约 |
| M2.1b | 需要重构 | `app/agent/context_engine/contracts.py` | 在现有 `ContextRequest` 末尾增加 `runtime_context: RuntimeContext | None = None`；不创建第二套请求或输出 DTO | M2.1a |
| M2.2 | 需要新增 | `app/agent/harness/context_service.py` | 状态到请求的纯 DTO 投影，不创建 Engine Adapter | M2.1b |
| M2.3 | 需要重构 | `app/agent/context_engine/compiler.py` | 运行态背景消息、隔离标记和真实 token 计数 | M2.1b |
| M2.4 | 需要重构 | `app/agent/context_engine/engine.py` | 基础预算包含运行态；统一 `start_build()`、请求校验、会话归属、显式附件访问校验和失败审计边界 | M2.3 |
| M2.5a | 需要新增 | `tests/test_harness_context.py` | 状态投影、身份隔离、裁剪、恢复重建和大对象排除测试 | M2.1a~M2.4 |
| M2.5b | 需要重构 | `tests/test_context_engine.py` | `runtime_context=None` 旧行为回归、运行态消息和预算测试 | M2.1b~M2.4 |

**单元测试**：

- `RuntimeContext` 及 `RuntimeCondition` 的 extra forbid、空目标、字段长度、列表上限、JSON 往返和默认容器隔离。
- `RuntimeContext` 不得携带身份字段；`resolved_conditions` 只能接收有界字符串，未确认条件和任意嵌套对象必须被拒绝或裁剪。
- `HarnessControlState -> RuntimeContext` 的映射；未确认的 `pending_confirmation` 不得进入 `resolved_conditions`。
- `ContextRequest.runtime_context=None` 时现有测试全部保持；有运行态时只增加受控背景消息，不改变系统指令。
- 系统指令、运行态消息和 query 一起计入预算；基础输入超预算时不调用后续 Planner。
- `ToolResult.result_ref/evidence_refs -> RuntimeObservation.result_ref/evidence_refs`；M2 只传递受控引用，不读取完整结果。

**集成测试**：

- Fake `ContextBuilder` 收到正确的用户、会话、附件和运行态字段，并返回现有 `CompiledContext`。
- 真实 `MemoryContextReader` 仍从 Checkpointer 读取 Working Memory，并执行用户身份校验。
- 工具观察改变下一次 `runtime_context`；恢复时使用原 `thread_id`、`turn_id`、`run_id`，不调用新 run 初始化。
- `ContextStore.start_build/finish_build/fail_build` 继续写审计元数据，不保存运行态正文和完整 `CompiledContext`。
- 请求校验失败、会话权限失败、显式附件越权和可重试依赖失败分别映射到稳定错误码；`start_build()` 前置权限失败不得被误记为已完成构建。
- ContextEngine 失败只回交受控错误给 Loop Controller，不直接改变 Harness 终态。

**M2 验收标准**：

1. 现有 `ContextEngine.build()` 和现有 `CompiledContext` 仍是唯一入口和唯一输出。
2. 新建、工具结果、错误重规划和确认恢复都能生成包含最新运行态摘要的上下文。
3. `runtime_context=None` 的旧调用行为不变；用户身份、conversation/thread 约束和附件权限测试通过。
4. 运行态、系统指令和 query 的 token 计数正确；基础输入超预算时不调用 Planner。
5. 不传完整 AgentState、SQL rows、源码或异常对象；Context trace 仍由现有 PostgreSQL ContextStore 审计。
6. M2 单元和集成测试通过后，才进入 M3；M2 不实现 Planning Agent、Tool Runtime、暂停接口或 Loop Controller。

**完善判定**：M2 已覆盖现有 `ContextEngine.build()` 的唯一入口、`RuntimeContext` 投影、五类运行态字段、预算、RAG/Memory 边界、错误分类、身份与附件访问校验、字段流、文件任务、测试和进入 M3 的门槛，文档层面判定为完善；代码层面仍属于需要重构/新增，必须完成 M2.1a～M2.5b 并通过测试后才算实现完成。

## 9. Planning Agent

> 架构模块 3；本章小节沿用模块内编号，研发阶段编号见第 21 节。

> 本节是 M3 的文档设计基线。M3 只定义统一 Planning Agent 的输入、结构化输出和动作校验边界；不实现 Tool Runtime、Loop Controller、真实暂停恢复或 Finalization。

### 3.1 模块职责

**一句话职责**：读取一次 Harness 循环生成的 `CompiledContext`、受控 `PlannerStateView` 和当前已注册的 `ToolSpec`，先生成并校验动作草稿，再由 Harness 发行候选序号并输出尚未提交、尚未执行的 `NextAction`。

**现有能力**：

- `app/agent/nodes/route_question.py::route_question()` 已使用 `PydanticOutputParser` 解析 `RouteDecision`，并在 LLM 失败时回退到 `single_query`。
- `app/agent/nodes/plan_analysis.py::plan_analysis()` 已使用 `PydanticOutputParser` 解析 `AnalysisPlan`，校验任务数量、任务 ID 和前置依赖，并兼容流式 LLM 调用。
- `app/agent/prompts/prompt_loader.py::load_prompt()` 已是现有 Prompt 加载入口。
- `app/agent/harness/contracts.py` 已有基础 `ActionType`、`ToolCall`、`ToolSpec`、`PlannerStateView`、`PlannerInput` 和 `NextAction` Pydantic 模型。

**需要重构**：

- 将现有 `PlannerInput.context: Any` 收紧为现有 `CompiledContext`，并把 `tools` 统一命名为 `tool_specs`，与 `ToolRegistry.list_specs()` 的输出一致。
- 将 `ActionType` 从当前仅有 `tool_call`、`final_answer` 扩展为三种动作；`NextAction` 增加结构化 `ask_user` payload，并保持 payload 互斥校验。
- 将模型输出、草稿校验、正式动作归一化和序号发行拆成明确阶段；LLM 不产生 `action_id` 或 `action_seq`。
- 把 Planner 的错误处理从旧节点自行回退改为可被 Loop Controller 识别的 `RunError(category=ErrorCategory.PLANNER)` 契约。Planner 不自行递增重试计数。

**需要新增**：

- `app/agent/harness/planning.py`：统一 `PlanningAgent` 实现边界。
- `app/agent/harness/action_validator.py`：对输出动作、工具注册、参数 schema 和能力开关做二次校验。
- `app/agent/prompts/plan_next_action.prompt`：统一 Harness 的下一步动作 Prompt，继续通过 `load_prompt("plan_next_action")` 加载。
- `tests/test_harness_planning.py`：覆盖结构化输出、动作互斥、工具和权限边界。

**暂不修改**：`app/agent/nodes/route_question.py`、`app/agent/nodes/plan_analysis.py` 和 `app/agent/graph.py` 继续服务现有固定图，不在 M3 直接改造成 Harness Planner。

### 3.2 规划边界

Planning Agent 负责理解当前目标、读取已确认条件和计划进度、识别信息缺口、从传入的 ToolSpec 中选择高层工具、判断是否需要用户澄清，以及判断证据是否足以生成最终答案。LLM/解析器的直接输出是无 `action_id`、无 `action_seq` 的 `PlannerActionDraft`；动作校验通过后，Planner 再由 Harness 生成带候选 `action_seq` 和 `action_id` 的待提交 `NextAction`，但不执行动作、不写入状态。

Planning Agent 不负责执行工具、访问数据库或业务仓储、生成/执行 SQL、执行 Python、写 Memory、写 Checkpoint、管理重试、控制循环、暂停恢复或保存隐藏思考。`rationale_summary` 如果保留，只能是短的面向审计的理由摘要，不是 reasoning 转储。

### 3.3 输入 DTO 与 Protocol 契约

M3 的输入由 M2 的 `CompiledContext`、M1 的 `PlannerStateView` 和 M4 `ToolRegistry.list_specs()` 的结果组成。目标接口如下，代码只表达边界，不在本模块实现业务逻辑。LLM/解析器阶段的结果是无 Harness 元数据的 `PlannerActionDraft`；对外 `PlanningAgent.plan()` 只返回经过校验、已发行候选 `action_seq` 的待提交 `NextAction`。这里的“已发行”只表示已完成 Harness 侧 ID/序号归一化，不表示已写入状态或已执行工具；真正的原子提交由 M5 负责。

| 输入字段 | 类型 | 来源 | 校验与用途 | 持久化边界 |
| --- | --- | --- | --- | --- |
| `compiled_context` | `CompiledContext` | M2 `ContextEngine.build()` | 只读；必须是本轮最新且 `trace.status == "completed"` 的上下文快照 | 仅临时模型输入，不写入 Checkpointer |
| `state_view` | `PlannerStateView` | M1 状态层投影 | 只含目标、迭代、计划摘要、观察和受控错误；不传完整 `AgentState` | 只读投影，不单独持久化 |
| `tool_specs` | `tuple[ToolSpec, ...]` | M4 Registry 的已注册工具快照 | 必填；只能选择其中已注册且启用的工具，参数以 `input_schema` 为准；当前不做用户级工具授权过滤 | 只保存必要的 schema 摘要/哈希，不保存认证或授权上下文 |

```python
from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from app.agent.context_engine.contracts import CompiledContext
from app.agent.harness.contracts import (
    ActionType,
    ContractModel,
    ErrorCategory,
    NextAction,
    PlannerInput,
    PlannerCapabilities,
    PlannerStateView,
    RunError,
    ToolCall,
    ToolSpec,
)
from app.agent.harness.tools.contracts import JsonSchemaValidator


class ToolCallDraft(ContractModel):
    """LLM 动作草稿中的工具调用，不允许模型生成 action_id。"""

    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0)


class AskUserDraft(ContractModel):
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
    """LLM 解析阶段的动作草稿；正式 action_id/action_seq 由 Harness 生成。"""

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
        if self.action_type is ActionType.FINAL_ANSWER and self.final_answer is None:
            raise ValueError("final_answer 草稿必须包含非空 final_answer")
        return self


class ActionIdFactory(Protocol):
    def issue(self, *, run_id: str, iteration: int, action_seq: int) -> str:
        """由 Loop Controller 所属的 Harness 生成稳定且唯一的 action_id。"""


class ActionIssuanceContext(ContractModel):
    """Loop Controller 提供的非 Prompt 元数据，仅用于生成动作 ID。"""

    run_id: str = Field(min_length=1)
    iteration: int = Field(ge=0)
    # 候选正式动作序号；由 Loop Controller 从已提交序号加一分配。
    action_seq: int = Field(ge=1)


class ActionNormalizer(Protocol):
    def normalize(
        self,
        *,
        draft: PlannerActionDraft,
        action_id_factory: ActionIdFactory,
        issuance: ActionIssuanceContext,
    ) -> NextAction:
        """把无幂等 ID 的 Planner 草稿转为带候选序号的待提交 NextAction。"""
        # 必须使用 issuance.action_seq 生成 NextAction.action_seq；不得接受草稿中的 ID。


class ActionCommitRequest(ContractModel):
    """由 Loop Controller 提交已校验动作的最小请求。"""

    run_id: str = Field(min_length=1)
    action: NextAction
    expected_action_seq: int = Field(ge=1)
    expected_state_version: int = Field(ge=0)
    # Harness 协调层修订号；映射 Saver checkpoint_id，不假设 Saver 原生支持整数 CAS。
    expected_checkpoint_version: int = Field(ge=0)
    execution_fence: RunExecutionFence

    @model_validator(mode="after")
    def validate_expected_seq(self) -> "ActionCommitRequest":
        if self.action.action_seq != self.expected_action_seq:
            raise ValueError("expected_action_seq 必须与 action.action_seq 一致")
        return self


class ActionCommitResult(ContractModel):
    """动作提交成功后的结果；失败通过 ActionCommitFailure 携带 RunError 抛出。"""

    status: Literal["committed", "idempotent"]
    action_seq: int = Field(ge=1)
    action_type: ActionType
    action_id: str | None = Field(default=None, min_length=1)


class ActionCommitFailure(Exception):
    """提交未完成时的实现层异常；只携带统一 RunError。"""

    error: RunError

    def __init__(self, error: RunError) -> None:
        self.error = error
        super().__init__(error.message)


class ActionCommitter(Protocol):
    async def commit(self, request: ActionCommitRequest) -> ActionCommitResult:
        """
        通过 prepared -> checkpoint -> committed 协议提交动作、action_seq、幂等记录和 checkpoint。
        expected_state_version 与 execution_fence 必须同时通过 CAS/fencing 校验；
        只有 committed 或已对账的 idempotent 动作才能下游可见；失败抛出 ActionCommitFailure(RunError)。
        """


class PlannerFailure(Exception):
    """实现层异常；只携带统一的 RunError，不构成第二套错误 DTO。"""

    error: RunError

    def __init__(self, error: RunError) -> None:
        self.error = error
        super().__init__(error.message)


class PlannerModelClient(Protocol):
    async def complete(self, prompt: str) -> str:
        """调用注入的 LLM；超时、运输失败必须转换为统一的 RunError(PLANNER)。"""


class PlannerInputFactory(Protocol):
    def build(
        self,
        *,
        compiled_context: CompiledContext,
        state_view: PlannerStateView,
        tool_specs: tuple[ToolSpec, ...],
    ) -> PlannerInput:
        """只组装并校验 PlannerInput，不执行检索或工具。"""


class PlanningAgent(Protocol):
    async def plan(
        self,
        input: PlannerInput,
        *,
        issuance: ActionIssuanceContext,
    ) -> NextAction:
        """完成草稿解析、校验、ID 发行和归一化，返回带候选序号的待提交动作。"""


class AnswerEligibilityDecision(ContractModel):
    eligible: bool
    error: RunError | None = None

    @model_validator(mode="after")
    def validate_error(self) -> "AnswerEligibilityDecision":
        if self.eligible and self.error is not None:
            raise ValueError("eligible=True 时不能携带 error")
        if not self.eligible and self.error is None:
            raise ValueError("eligible=False 时必须携带 error")
        return self


class AnswerEligibilityChecker(Protocol):
    def can_finalize(
        self,
        *,
        draft: PlannerActionDraft,
        planner_input: PlannerInput,
        capabilities: PlannerCapabilities,
    ) -> AnswerEligibilityDecision:
        """按确定性规则判断 final_answer 是否具备上下文和证据。"""


class PlannerRetryPolicy(ContractModel):
    """由 Loop Controller 使用的 Planner 重试策略；Planner 本身不计数。"""

    max_retries: int = Field(default=2, ge=0, le=5)
    retryable_codes: tuple[Literal[
        "planner_unavailable",
        "invalid_output",
        "invalid_action",
        "unknown_tool",
        "invalid_tool_arguments",
        "insufficient_evidence",
    ], ...] = (
        "planner_unavailable",
        "invalid_output",
        "invalid_action",
        "unknown_tool",
        "invalid_tool_arguments",
        "insufficient_evidence",
    )
    feedback_max_chars: int = Field(default=1_000, ge=0, le=4_000)
```

`PlannerInput` 沿用第 6.3 节的唯一核心 DTO 定义，不在 M3 重复声明；其字段固定为 `compiled_context: CompiledContext`、`state_view: PlannerStateView` 和 `tool_specs: tuple[ToolSpec, ...]`。字段约束：`compiled_context` 是唯一上下文快照，不允许传整个 `AgentState`；`state_view` 只允许受控摘要、进度、观察、错误和确认条件；`tool_specs` 必须来自调用方的 Registry 启用工具快照，Planner 不自行发现工具，也不得臆造 schema。

### 3.3.1 用户权限边界

当前阶段**不校验用户级工具权限**。代码基线中未发现统一认证、RBAC 或 ACL 服务，因此 `ToolSpec.permission` 只能作为未来预留的能力描述，不能被解释为当前授权结果，也不能把缺失的权限服务伪装成已实现能力。

当前 M3/M4 的实际边界只有：Registry 返回已注册且 `enabled=True` 的工具；ActionValidator 校验工具名称、能力开关和 `input_schema`；Tool Runtime 校验 `run_id`、`user_id`、`conversation_id`、`thread_id` 的运行归属和结果隔离。这些身份一致性校验不是用户工具权限校验。

用户级工具授权上下文和授权检查器仅作为未来扩展的 Protocol/DTO 预留，当前不进入 `PlannerInput`、`ToolExecutionRequest`、Prompt、`CompiledContext`、Checkpoint、SSE 或 M4 执行门禁。待认证授权方案落地后，必须单独补充工具列表过滤、执行前授权复核、审计和测试；在此之前不能写“权限过滤”“授权复核”或“缺失授权源 fail-closed”是当前能力。

`ask_user` 是动作契约，不等于已经暂停。只有 M5 识别该动作后才创建 `ConfirmationRequest`、保存 Checkpoint 并转入 `waiting_confirmation`。M3 阶段可以验证该 DTO，但生产能力开关默认关闭。

`PlannerCapabilities` 沿用 3.3 中的同一契约，不在本节重复定义；其中 `allow_tool_call`、`allow_ask_user` 和 `allow_final_answer` 是三类动作开关，`allow_context_only_final_answer` 是最终答案资格的附加限制。

`allow_context_only_final_answer` 只允许在当前 `CompiledContext` 有效、没有阻塞条件且业务场景明确允许直接基于上下文回答时使用；它不能绕过数据证据要求，也不能把 Planner 自己生成的文本当作工具观察。

### 3.4 输出 DTO 与动作不变量

**需要重构**：`ActionType` 目标值为 `tool_call`、`ask_user`、`final_answer`，并按第 6.3 节唯一的 `AskUserRequest`、`NextAction` DTO 完成三路 payload 互斥校验。M3 不重复定义这些核心 DTO；第 6.3 节是跨模块类型来源，本节只补充 Planner 草稿、候选序号和提交边界。

`ask_user` 不携带 `confirmation_id`：确认请求 ID 由 M5 创建和持久化，避免 Planner 伪造恢复凭证。第 6.3 节已经定义的 `NextAction` 是本模块唯一使用的动作 DTO；其 `action_seq` 在提交前是候选序号，提交成功后才成为所有正式动作共有的顺序号。`tool_call.action_id` 由 Harness 侧根据 `run_id + iteration + action_seq` 生成，不能由 LLM 通过重复输出制造幂等冲突。`final_answer` 不是数据库写入结果，最终保存和 Memory Formation 由 M6 负责。

### 3.4.1 输出持久化边界

`PlannerActionDraft` 只存在于一次 Planner 调用的临时内存中，不进入 Checkpointer、PostgreSQL 或 SSE；完整 raw output、隐藏 reasoning 和 Prompt 也不保存。校验通过后，`ActionNormalizer` 使用 `ActionIssuanceContext` 生成带候选 `action_seq` 的待提交 `NextAction`，并为 `tool_call` 生成 `action_id`；此时仍未写入 `HarnessControlState`、数据库或 checkpoint。正式动作的结构化摘要、动作类型、动作 ID、参数摘要/哈希和提交状态由 M5 的 `ActionCommitter` 在提交边界处理。

`tool_call` 的受控参数由 M4 接收并再次校验，完整工具结果不回写 Planner 输出；`ask_user` 只把问题和原因交给 M5 创建 `ConfirmationRequest`；`final_answer` 交给 M6 的 `FinalizationInput`，最终文本和结果引用由 M6 保存到会话结果表并返回 API/SSE。

### 3.4.2 输出字段与动作提交边界

`PlanningAgent.plan()` 返回的是候选动作。只有 `M5 ActionCommitter.commit()` 成功后，`NextAction` 才能被称为本次 run 的正式动作，并允许进入 M4、M5 或 M6 的下游处理。M3 不得通过返回对象、事件或回调隐式完成提交。

| 输出字段 | 生成与校验方 | 提交前状态 | 提交成功后的规则 | 持久化与下游用途 |
| --- | --- | --- | --- | --- |
| `action_type` | `PlannerActionDraft` 经 `ActionValidator` 校验 | 候选动作类型；只能是三类之一 | 写入正式动作记录，并决定唯一的下游分支 | M5 动作记录；按类型分派到 M4、M5 或 M6 |
| `action_seq` | M5 `ActionIssuanceContext` + `ActionNormalizer` | 候选序号，必须等于 `expected_action_seq` | 经 prepared/checkpoint/committed 对账后推进；candidate checkpoint 不授予执行权 | M5 状态、动作记录和 checkpoint 的一致性与顺序控制 |
| `tool_call.action_id` | Harness 注入的 `ActionIdFactory` | 由 `run_id + iteration + action_seq` 确定的候选幂等键 | 与工具动作记录一起提交；同一键同一 payload 重复提交只能幂等成功 | M5 幂等记录；提交成功后供 M4 识别动作身份 |
| `tool_call.arguments` | `ActionValidator` 按 `ToolSpec.input_schema` 校验 | 仅保留在受控内存对象中，不能因 Planner 返回就执行 | 提交成功后交给 M4；M4 执行前必须再次做 schema、状态和运行身份校验 | M4 工具调用输入；动作记录默认只保存脱敏摘要或哈希，完整参数按安全策略处理 |
| `ask_user` | `PlannerActionDraft` / `ActionValidator` | 问题、原因和 `required_fields` 的候选请求 | 提交成功后由 M5 创建 `ConfirmationRequest` 和恢复凭证，再进入 `waiting_confirmation` | M5 确认请求与 checkpoint；Planner 不生成 `confirmation_id` |
| `final_answer` | `ActionValidator` + `AnswerEligibilityChecker` | 只是一段待收尾文本，不等于已完成 run | 提交成功后交给 M6；M6 成功收尾后才写最终结果和终态 | M6 `FinalizationInput`；不得由 M3 直接写会话结果或 Memory |
| `rationale_summary` | Planner 草稿经长度限制校验 | 可选的短审计摘要，不是隐藏 reasoning | 随正式动作记录保存或按审计策略丢弃 | 仅用于受控审计与观测；不得保存完整 Prompt、raw output 或 reasoning |

`ActionCommitter.commit()` 的最小协调语义如下。由于业务 PostgreSQL 与 LangGraph `AsyncPostgresSaver` 不能共享同一个数据库事务，本协议不宣称跨资源事务原子性；它通过可验证的状态阶段和 digest 保证未完成动作不对下游可见，并支持崩溃后对账。

1. 校验 `request.run_id`、当前 run 状态、`expected_action_seq` 和 `request.action.action_seq`；序号不一致返回 `ErrorCategory.CONFLICT`。
2. 以“当前已提交序号仍为 `expected_action_seq - 1`”为条件，在业务库写入 `prepared` 动作记录、`action_id` 幂等记录和 payload digest；prepared 记录对 M4、确认流程和 M6 不可见。
3. Loop Controller 将相同的动作引用、payload digest、`action_seq` 和预期 fencing token 写入同一线程 checkpoint；checkpoint 是 prepared 动作可以继续提交的恢复证明。
4. ActionCommitter 再以 `prepared + checkpoint digest 匹配` 为条件将动作标记为 `committed`。只有 committed 动作才能进入 M4、确认流程或 M6；`HarnessControlState.action_seq` 以 checkpoint 中的已提交值为准。
5. 对同一 `run_id + action_seq` 重复提交时，如果动作类型、动作身份和受控 payload 摘要一致，返回幂等成功；如果内容或身份冲突，返回 `ErrorCategory.CONFLICT`，不得覆盖原动作。
6. 任一步骤失败时不得发送“动作已执行”的成功事件。恢复器发现无匹配 checkpoint 的 prepared 记录时将其标记为不可见/可清理；发现匹配 digest 时补记 committed；发现 digest、fencing token 或序号不一致时进入 conflict，不自动执行。
7. 只有 committed 或已对账的 idempotent 结果返回后，Loop Controller 才能根据 `action_type` 分派下游；M3 的 `ActionNormalizer` 不拥有该分派权限。

这里的“协调提交”要求实现层提供业务记录的条件更新、checkpoint digest 对账、fencing 校验和明确的可见性状态；不能把动作记录、状态序号、幂等记录和 checkpoint 拆成无条件的独立写入。该协议的下游可见状态只有 `committed`，`prepared` 仅用于恢复和审计。

### 3.5 Prompt、解析和动作校验

**需要新增**：`app/agent/prompts/plan_next_action.prompt`。Prompt 只描述高层动作选择和结构化输出约束：工具只能从 `tool_specs` 选择；参数必须符合 `input_schema`；证据不足时使用 `ask_user`；证据充分时才使用 `final_answer`；不得输出 SQL、Python 或隐藏思考。

```python
class PlannerOutputParser(Protocol):
    def parse(self, raw_output: str) -> PlannerActionDraft:
        """只把模型输出解析为无 ID 的动作草稿，不执行动作。"""


class ActionValidator(Protocol):
    def validate(
        self,
        draft: PlannerActionDraft,
        *,
        planner_input: PlannerInput,
        capabilities: PlannerCapabilities,
        answer_eligibility: AnswerEligibilityChecker,
        schema_validator: JsonSchemaValidator,
    ) -> None:
        """校验草稿、工具 schema、能力开关和最终答案资格；失败抛出 PlannerFailure。"""
```

动作校验至少包括：

| 检查 | 失败分类 | 默认策略 | 说明 |
| --- | --- | --- | --- |
| JSON/Pydantic 解析失败 | `planner / invalid_output` | 有限重试 | 重试提示包含 schema 错误，不把原始 reasoning 回传给用户 |
| `tool_name` 未在 `tool_specs` | `planner / unknown_tool` | 可带错误反馈重试一次 | 不得调用未注册工具 |
| `arguments` 不符合 `input_schema` | `validation / invalid_tool_arguments` | 可带校验错误重试 | 以注册表 schema 为准 |
| 工具权限 | 暂不启用 | 不进入当前 M3 校验 | `ToolSpec.permission` 只作为未来预留描述，当前不据此拒绝或放行工具 |
| `allow_tool_call=False` | `planner / planner_action_disabled` | 受控失败 | 能力关闭时不允许任何 `tool_call` |
| `ask_user` 能力未启用 | `planner / planner_action_disabled` | 交给 Loop Controller 降级 | M3 不创建暂停现场 |
| `allow_final_answer=False` | `planner / planner_action_disabled` | 受控失败 | 能力关闭时不允许 `final_answer` |
| `final_answer` 无可用证据或存在阻塞条件 | `planner / insufficient_evidence` | 有限重试或改为工具/澄清 | 不得仅凭 LLM 文本声称数据结论 |
| 三类 payload 不互斥 | `planner / invalid_action` | 有限重试 | 拒绝同时返回工具和答案 |

`ActionValidator` 的确定性顺序固定为：先校验 `PlannerActionDraft` 的 payload 和 `action_type`；再按 `draft.tool_call.tool_name` 查找 `planner_input.tool_specs`，找不到返回 `RunError(category=ErrorCategory.PLANNER, code="unknown_tool", ...)`；找到后把 `arguments` 交给 `JsonSchemaValidator.validate(instance=..., schema=tool_spec.input_schema)`，失败返回 `RunError(category=ErrorCategory.VALIDATION, code="invalid_tool_arguments", ...)`；随后检查 `PlannerCapabilities`，分别拒绝未启用的 `tool_call`、`ask_user` 或 `final_answer`；最后对 `final_answer` 调用 `AnswerEligibilityChecker`。`PlannerFailure` 只包装这一个 `RunError`，不得再创建第二套 Planner 错误 DTO。

当前 M3 只拒绝不在 Registry 已启用快照中的工具，不负责用户权限判断；空工具列表按能力配置处理，不得据此伪造答案。`ToolSpec.permission` 不参与当前动作校验，也不进入 Planner 重试路径。

`AnswerEligibilityChecker` 的确定性规则：`CompiledContext.trace.status == "completed"` 且 `messages` 非空；`PlannerStateView.plan_progress.blocked_reason` 为空；存在至少一个 `success` 或 `partial` 的 `RunObservation`，或者 `allow_context_only_final_answer=True` 且业务场景明确允许只基于上下文回答；观察的摘要、`result_ref` 或 `evidence_refs` 可追溯；不存在未解决用户条件、冲突口径或上下文构建错误。纯解释性问题可以走 context-only 分支，但不能把模型自己的输出当作证据。

资格失败返回 `RunError(category=ErrorCategory.PLANNER, code="insufficient_evidence", retryable=True, ...)`；若缺口需要用户输入，应由 Planner 选择 `ask_user`，而不是把不合格文本升级为最终答案。

### 3.6 核心伪代码

伪代码只描述 Planner 自身的输入、Prompt、解析和动作校验，不执行工具、不更新状态、不控制循环：

```text
async plan(input: PlannerInput, issuance: ActionIssuanceContext) -> NextAction:
    rendered_prompt = render(plan_next_action.prompt, input)
    raw_output = await injected_llm_client.complete(rendered_prompt)
    draft = output_parser.parse(raw_output)
    action_validator.validate(
        draft,
        planner_input=input,
        answer_eligibility=answer_eligibility_checker,
        schema_validator=json_schema_validator,
        capabilities=injected_capabilities,
    )
    return action_normalizer.normalize(
        draft=draft,
        action_id_factory=action_id_factory,
        issuance=issuance,
    )
```

动作链路固定为：`LLM raw output -> PlannerActionDraft -> ActionValidator -> ActionIdFactory -> ActionNormalizer -> NextAction`。

错误路径：解析失败、LLM 超时或运输失败统一转为 `RunError(category=ErrorCategory.PLANNER, code="invalid_output" 或 "planner_unavailable", ...)`，由 `PlannerFailure` 包装后交回 Loop Controller。`PlannerRetryPolicy.max_retries=2` 表示初始调用之外最多重试 2 次；`planner_retry_count` 由 M1/Loop Controller 维护，Planner 不自行递增。`invalid_output`、`invalid_action`、`unknown_tool`、`invalid_tool_arguments`、`planner_unavailable` 和 `insufficient_evidence` 默认可有限重试；能力未启用和不可恢复错误默认不可重试。重试反馈只包含截断后的结构/字段错误，不包含隐藏思考、完整 raw output、Prompt 或未纳入当前执行链路的授权上下文。成功后清零当前 Planner 重试计数，但不回退 `action_seq`；校验失败不调用 `ActionNormalizer`，因此不分配序号。重试耗尽后交由 Loop Controller 进入受控 `failed` 或安全降级/请求澄清，不伪造工具结果、证据或最终答案。Planner 不保存完整 raw output，长期状态只记录错误码、短消息、重试次数和必要哈希。

### 3.7 字段级数据流

| 上游模块 | 上游字段 | 当前模块如何消费 | 当前模块输出字段 | 下游模块 | 下游用途 |
| --- | --- | --- | --- | --- | --- |
| M2 ContextEngine | `CompiledContext` | 作为唯一上下文快照读取 | `PlannerInput.compiled_context` | M3 Planning Agent | 规划输入；正文仅临时使用，不进入 Harness 状态 |
| M1 状态层 | `original_goal`、`plan_progress`、`observations`、`last_error` | 投影为受控 Planner 视图 | `PlannerInput.state_view` | M3 Planning Agent | 读取目标、进度、证据摘要和受控错误 |
| M1 Harness 状态 | `user_id`、`conversation_id`、`thread_id` | 仅用于运行归属和上下文隔离，不作为权限依据 | 请求身份字段 | M4 Runtime/ArtifactStore | 校验同一运行和结果访问边界 |
| M4 Tool Registry | 已注册且启用的 `ToolSpec` | 作为当前可用能力快照；不做用户级工具授权过滤 | `PlannerInput.tool_specs` | Planner/ActionValidator | 工具选择和 `input_schema` 参数校验 |
| Planner LLM | raw output | 解析为 Pydantic 草稿并做 payload 校验 | `PlannerActionDraft` | ActionValidator | 临时校验；不持久化 raw output 或 reasoning |
| M3 ActionValidator | `PlannerActionDraft`、`ToolSpec.input_schema` | 校验工具、参数、能力开关和答案资格 | 已校验草稿 | ActionNormalizer | 只有成功校验的草稿才能发行动作序号 |
| M1 Loop Controller | `run_id`、`iteration`、已提交的 `state.harness.action_seq` | 计算 `expected_action_seq = action_seq + 1` 并提供发行上下文 | `ActionIssuanceContext` | M3 ActionNormalizer / M5 ActionCommitter | 只生成候选 `NextAction.action_seq`；M5 提交成功后才成为正式序号 |
| M3 ActionNormalizer | 已校验草稿和发行上下文 | 生成待提交动作，不执行副作用 | `NextAction` | M5 ActionCommitter | 提交成功后才允许按 `action_type` 分派 |
| M3 `NextAction.tool_call` | 待提交的工具动作 | M5 提交成功后交给 M4 做二次校验 | `ToolCall` | M4 Tool Runtime | 再做运行身份、状态、参数和超时校验后执行 |
| M3 `NextAction.ask_user` | 问题、原因和 `required_fields` | M5 提交成功后转为暂停请求 | `ConfirmationRequest` | M5 Loop Controller | 创建确认 ID、保存 checkpoint 并暂停 |
| M3 `NextAction.final_answer` | 通过资格校验的待收尾答案文本 | M5 提交成功后封装收尾输入 | `FinalizationInput` | M6 Finalization | 保存最终结果并提交 Memory Formation |

闭环字段流固定为：

```text
ToolResult -> RunObservation -> RuntimeContext -> CompiledContext
    -> PlannerInput -> NextAction -> Loop Controller
```

### 3.8 与现有代码的衔接点

| 代码位置 | 当前状态 | M3 处理 |
| --- | --- | --- |
| `app/agent/harness/contracts.py` | **现有能力**：基础 DTO 和 Pydantic 校验 | **需要重构**：收紧 `PlannerInput`、统一 `tool_specs`、增加 `ASK_USER` 和 `AskUserRequest` |
| `app/agent/harness/tools/registry.py` | **需要新增（M4）**：当前未发现统一 Tool Registry | **需要新增**：维护名称唯一、版本明确且已启用的 `ToolSpec` 快照；不能把 permission 字符串当作当前授权结果 |
| `app/agent/harness/tools/authorization.py` | **暂不启用**：当前未发现统一用户权限方案 | **未来扩展**：认证授权方案确定后再设计独立授权契约；不作为 M3/M4 当前前置条件 |
| `app/agent/context_engine/contracts.py` | **现有能力**：正式 `CompiledContext` | 直接作为 `PlannerInput.compiled_context` 类型，不复制 DTO |
| `app/agent/nodes/route_question.py` | **现有能力**：固定图路由 Planner | **暂不修改**：不当作统一 Planning Agent |
| `app/agent/nodes/plan_analysis.py` | **现有能力**：分析任务 Planner | **暂不修改**：`AnalysisPlan` 不是 `NextAction` |
| `app/agent/prompts/prompt_loader.py` | **现有能力**：Prompt 加载入口 | 直接复用加载 `plan_next_action.prompt` |
| `app/agent/context.py` | **现有能力**：注入 LLM 和业务仓储 | **待验证/最小修改**：不把仓储暴露给 Planner |
| `app/agent/graph.py` | **现有能力**：固定图 | **暂不修改**：Loop Controller 由 M5 接入 |

不创建 `PlanningAgentAdapter`、`RoutePlannerAdapter` 或 `AnalysisPlannerAdapter`。旧节点与统一 Planner 的输出目标不同，强行复用会制造虚假的 Harness 动作。

### 3.9 文件级任务与测试验收

**需要新增**：`app/agent/harness/planning.py`、`app/agent/harness/action_validator.py`、`app/agent/prompts/plan_next_action.prompt` 和 `tests/test_harness_planning.py`。测试只使用假的 LLM 和 ToolSpec，不连接真实数据库或外部服务。

**需要重构**：`app/agent/harness/contracts.py` 补齐 `ASK_USER`、`AskUserRequest`、`PlannerCapabilities`、`tool_specs` 和严格 payload 校验；`app/agent/harness/state.py` 只提供状态投影来源，重试计数继续归 Loop Controller。

**M3 验收标准**：

- 合法 `tool_call`、`ask_user`、`final_answer` 均能通过 Pydantic 解析；混合 payload、空答案和空问题被拒绝。
- `PlannerInput` 只接受 `CompiledContext`、`PlannerStateView` 和 tuple 化 `tool_specs`；不接受完整 `AgentState` 或任意 `context`。
- 解析失败、LLM 超时、未知工具、参数不匹配和 `ask_user` 未启用分别产生稳定错误分类；用户级权限当前不参与 M3 运行。
- 解析器不把 reasoning 事件写入 `NextAction`；Planner 不调用 Tool Runtime、数据库、Memory 或 Checkpointer。
- Planner 本身不修改 iteration、重试计数和 Harness 状态；旧 route、analysis、固定图测试保持通过。
- `allow_tool_call`、`allow_ask_user`、`allow_final_answer` 逐一覆盖开启和关闭场景；关闭任一能力时对应动作均被拒绝，且不进入下游分派。
- `allow_context_only_final_answer=True` 只能在有效 `CompiledContext`、无阻塞条件且业务策略允许时放行；没有有效观察或可追溯证据时，`final_answer` 必须被拒绝。
- `ActionNormalizer` 生成的 `NextAction.action_seq` 只作为候选值；解析失败、草稿校验失败、Planner 异常和所有重试均不推进 `HarnessControlState.action_seq`，且不生成新的正式动作记录。
- `ActionCommitRequest.expected_action_seq` 与 `action.action_seq` 不一致时被拒绝；并发提交或旧序号提交返回 `ErrorCategory.CONFLICT`，不能覆盖已提交动作。
- 提交成功的动作类型、动作序号、`tool_call.action_id`、动作记录、幂等记录和 checkpoint 可被一致读取；重复提交相同动作幂等成功，payload 冲突不得覆盖原记录。
- 正式动作提交任一写入失败时，动作、序号、幂等记录和 checkpoint 不对下游可见；M4、M5 确认流程和 M6 Finalization 均未被调用，也不发送动作成功事件。
- M4 执行 `tool_call` 前再次按注册表 `input_schema` 校验 `tool_call.arguments`，并校验运行身份和状态阶段；当前不执行用户级工具授权判断。
- `PlannerRetryPolicy.max_retries=2` 的测试明确包含 1 次初始调用和最多 2 次重试；重试耗尽后只返回受控失败或请求澄清，不伪造工具结果、证据或最终答案。

**完善判定**：M3 已覆盖职责边界、`PlannerInput`/`PlannerStateView`/`NextAction`、三类动作、结构化解析、动作校验、序号发行、提交边界、错误与重试、权限范围声明、字段流、文件任务、测试和进入 M4 的门槛，文档层面判定为完善；代码层面仍属于需要重构/新增，必须完成 M3 任务并通过测试后才算实现完成。

M3 通过 DTO、Protocol、错误和单元测试后，才进入 M4 Tool Runtime；真实循环、动作重试、暂停恢复和最终收尾仍未完成。

## 10. Tool Runtime

> 架构模块 4；本章小节沿用模块内编号，研发阶段编号见第 21 节。

> 本节是 M4 的文档设计基线。M4 只定义高层工具的注册、执行、结果归一化和幂等边界；不负责任务规划、循环调度、暂停恢复或最终会话收尾。

### 4.1 模块职责

**一句话职责**：接收 M5 已提交且尚未执行的 `ToolCall`，在运行状态、运行身份、参数和超时均通过校验后，调用现有业务能力或新增共享 Service，并返回统一 `ToolResult`。当前阶段不执行用户级权限校验。

**现有能力**：

- `app/agent/query_graph.py::query_graph` 已是可复用的问数子图，能够完成关键词抽取、四路 Meta 召回、上下文过滤、SQL 生成、SQL 执行和结果增强。
- `app/agent/nodes/execute_analysis.py::execute_analysis` 已完成分析任务的依赖分层、同层并行、Query Agent 调用、数据画像、Python 计算和分析证据汇总。
- `app/agent/nodes/generate_report_plan.py::generate_report_plan` 和 `app/agent/nodes/render_report.py::render_report` 已形成报告规划与真实数据绑定链路。
- `MetaCatalogRepository`、四类 Qdrant Meta 仓储、`DimensionValueSearch` 和 `DwRepository` 已提供数据目录检索、维度值检索和 DW 查询基础能力。

**需要重构**：

- 把旧节点中的可复用业务逻辑提取为 `DataCatalogService`、`QueryService`、`AnalysisService`、`ReportService`；旧固定图继续调用这些 Service，Harness 工具也调用同一 Service。
- 统一 `ToolSpec`、`ToolCall`、`ToolResult` 的字段来源。M4 不创建第二套同名 DTO；第 6.3 节是唯一核心定义。
- 对当前直接返回完整 `sql_result`、分析 `rows` 和 `python_code` 的旧状态路径增加 Harness 结果外置边界。Harness 只把摘要和引用写入运行状态。
- 为 `DwRepository.execute_query()` 增加只读 SQL、语句超时、结果上限和取消语义；当前代码尚未提供这些保护。

**需要新增**：

- `ToolRegistry`、`ToolDispatcher`、`ToolRuntime`、`ResultNormalizer` 和执行幂等记录。
- 各高层工具的 Pydantic 输入 DTO、超时策略和结果引用存储 Protocol。
- `ResultArtifactStore` 或等价的结果存储边界，用于保存完整 rows、分析产物和报告结构；当前仓库未发现独立 ArtifactStore。

**暂不启用**：`knowledge_base`。当前仓库没有可靠的企业制度、业务规则、分析规范和文档知识库；Meta/Qdrant/ES 目录检索只能作为 `data_catalog`，不能注册为企业知识库工具。

**不负责**：决定调用哪个工具；生成 SQL 或 Python 计划；修改完整 Harness 状态；递增 Planner 或 Loop 重试次数；创建确认 ID；直接写长期 Memory；直接写会话最终答案；把原始执行事件转成 SSE；绕过 M5 的动作提交边界。

### 4.2 输入 DTO 与执行前校验

M4 的正式输入来自 M5 的动作提交结果。Planner 生成的 `NextAction` 只有在 M5 `ActionCommitter` 成功后，才允许转换为 `ToolExecutionRequest`；M4 不接受未提交动作，也不从模型输出中自行创建 `action_id`。

| 输入 | 类型 | 来源 | 必填 | 校验与用途 |
| --- | --- | --- | --- | --- |
| `run_id` | `str` | M1/M5 | 是 | 非空；必须对应当前运行，不能由工具参数覆盖 |
| `user_id` | `str` | M1 | 是 | 非空；仅用于运行归属、结果隔离和产物访问绑定，不作为用户级权限判断依据 |
| `conversation_id` | `str` | M1 | 是 | 非空；只用于会话归属和结果访问边界 |
| `thread_id` | `str` | M1 | 是 | 必须与当前 Checkpointer 线程一致；工具不能切换线程 |
| `tool_call` | `ToolCall` | M3/M5 | 是 | `tool_name` 必须精确匹配 Registry；`action_id` 是幂等主键 |
| `state_status` | `HarnessStatus` | M1 | 是 | 只有 `running` 可以执行；`waiting_confirmation` 和所有终态拒绝执行 |
| `state_phase` | `LoopPhase` | M1/M5 | 是 | 只允许在 `execute_tool` 阶段执行，其他阶段返回状态冲突 |
| `attempt` | `int` | M5 `tool_retry_counts[action_id]` | 是 | 首次为 0；仅同动作临时错误重试时加 1，重复 attempt 不得再次调用 handler |
| `agent_context` | `AgentContext` | AgentService 装配 | 否（运行时依赖） | 不属于可序列化 DTO；只作为请求级依赖注入，不写入 Checkpoint，不进入 Planner Prompt |

具体工具参数必须先按 `ToolSpec.input_schema` 做 JSON Schema 校验，再由工具输入 DTO 做 Pydantic 校验。两层校验都通过后才调用 handler；未知工具、禁用工具、缺少必填字段、额外字段、类型错误和超过上限的参数均不得触发业务副作用。

以下输入 DTO 是 M4 的目标契约示例，不是当前已实现代码。它们只描述工具参数，不包含用户权限、完整 AgentState、LLM Prompt、SQL rows 或 Python 源码：

```python
from typing import Literal

from pydantic import Field, model_validator

from app.agent.harness.contracts import ContractModel


CatalogSearchType = Literal[
    "metrics",
    "tables",
    "columns",
    "dimensions",
    "dimension_values",
    "relationships",
]


class DataCatalogInput(ContractModel):
    query: str = Field(min_length=1, max_length=1_000)
    search_types: tuple[CatalogSearchType, ...] = (
        "metrics",
        "tables",
        "columns",
        "dimensions",
        "dimension_values",
        "relationships",
    )
    limit: int = Field(default=10, ge=1, le=50)
    include_relationships: bool = True


class QueryDataInput(ContractModel):
    question: str = Field(min_length=1, max_length=8_000)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    max_rows: int = Field(default=200, ge=1, le=10_000)
    include_display_names: bool = True


class AnalyzeDataInput(ContractModel):
    question: str = Field(min_length=1, max_length=8_000)
    analysis_goals: tuple[str, ...] = Field(default=(), max_length=8)
    max_tasks: int = Field(default=8, ge=1, le=8)
    max_rows_per_task: int = Field(default=200, ge=1, le=10_000)


class BuildReportInput(ContractModel):
    source_task_ids: tuple[str, ...] = Field(default=(), max_length=8)
    title_hint: str = Field(default="", max_length=200)
    requested_components: tuple[
        Literal["text", "kpi", "table", "chart"], ...
    ] = ()
    max_rows_per_component: int = Field(default=200, ge=1, le=1_000)


class KnowledgeBaseInput(ContractModel):
    query: str = Field(min_length=1, max_length=2_000)


class RuntimePolicy(ContractModel):
    """工具运行时的硬限制；由应用配置注入，不能由 Planner 覆盖。"""

    default_tool_timeout_seconds: int = Field(default=60, gt=0)
    tool_timeout_ceiling_seconds: int = Field(default=300, gt=0)
    max_inline_summary_chars: int = Field(default=4_000, gt=0)
    max_result_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    max_evidence_refs: int = Field(default=32, gt=0)

    @model_validator(mode="after")
    def validate_timeout_bounds(self) -> "RuntimePolicy":
        if self.default_tool_timeout_seconds > self.tool_timeout_ceiling_seconds:
            raise ValueError("默认工具超时不能超过工具最大超时")
        return self
```

`knowledge_base` 的输入 DTO 可以先用于 schema 版本兼容测试，但 `ToolSpec.enabled` 必须为 `False`，Registry 不得将其返回给 Planner，也不得接受其执行请求。

### 4.3 ToolSpec、Registry 与能力边界

`ToolSpec` 是给 Planning Agent 的能力描述，不是当前授权结果。当前阶段不校验用户级 RBAC/ACL，Registry 和 Runtime 不接入用户工具授权服务。Registry 只返回已注册且 `enabled=True` 的工具；M4 当前执行前只校验工具名称、参数、运行状态、阶段、运行身份一致性、超时和幂等边界。`ToolSpec.permission` 保留为未来接入认证授权方案时的描述字段，不能用它、`user_id` 或历史缓存推断当前用户已授权。

Registry 的职责是维护名称唯一、版本明确、启用状态可控的工具快照，并把同一份 `ToolSpec.input_schema` 提供给 Planner 和 Runtime。Registry 不执行工具、不读取业务数据库、不修改 Harness 状态。

| 工具名 | `enabled` | `permission` 目标值 | `idempotency` | `result_kind` | 状态 |
| --- | --- | --- | --- | --- | --- |
| `data_catalog` | `True` | `data.catalog.read` | `idempotent` | `artifact` | **需要新增**高层注册；底层目录能力现有 |
| `query_data` | `True` | `data.query.read` | `idempotent` | `artifact` | **需要新增**高层名称；内部复用 Query 子图 |
| `analyze_data` | `True` | `data.analysis.execute` | `conditionally_idempotent` | `artifact` | **需要新增**高层名称；内部复用 Analysis/Sandbox |
| `build_report` | `True` | `data.report.build` | `conditionally_idempotent` | `report` | **需要新增**高层名称；内部复用 Report 节点逻辑 |
| `knowledge_base` | `False` | `knowledge.search` | `idempotent` | `artifact` | **暂不启用**；没有企业知识库实现 |

`permission` 只描述未来可能需要的授权，不参与当前执行决策，也不应放进 Prompt 让模型自行判断。Planner 看到的是 Registry 的已注册启用工具快照；用户身份字段只用于运行归属和结果隔离，不进入 `PlannerInput`、`CompiledContext`、Checkpoint、SSE 或结果摘要。未来接入认证授权时，必须先扩展独立的授权契约和审计方案，不能把本字段直接升级成运行时权限判断。

### 4.4 Tool Protocol 与执行上下文

Tool Protocol 只表达高层工具的执行边界，不实现业务逻辑。工具 handler 接收已经通过 Registry、Schema、状态、阶段和运行身份校验的请求，返回仅在 M4 内部使用的 ToolHandlerResult；只有 ResultNormalizer 产生的 ToolResult 才能交给 M5。

~~~python
from datetime import datetime
from typing import Any, Protocol

from pydantic import Field, model_validator

from app.agent.context import AgentContext
from app.agent.harness.contracts import (
    ContractModel,
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
    RunExecutionFence,
    ToolCall,
    ToolResult,
    ToolSpec,
)
class ToolExecutionRequest(ContractModel):
    run_ref: HarnessRunRef
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    allowed_asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    tool_call: ToolCall
    attempt: int = Field(default=0, ge=0)
    state_status: HarnessStatus
    state_phase: LoopPhase
    execution_fence: RunExecutionFence


class ToolHandlerResult(ContractModel):
    """handler 临时结果，不得直接进入 Harness 状态或 API。"""

    payload: Any = None
    summary: str = Field(default="", max_length=4_000)
    evidence_payloads: tuple[Any, ...] = ()
    limitations: tuple[str, ...] = ()


class JsonSchemaValidator(Protocol):
    def validate(
        self,
        *,
        instance: dict[str, Any],
        schema: dict[str, Any],
    ) -> None: ...


class Tool(Protocol):
    spec: ToolSpec

    async def execute(
        self,
        call: ToolCall,
        *,
        request: ToolExecutionRequest,
        dependencies: AgentContext,
    ) -> ToolHandlerResult: ...


class ToolRegistry(Protocol):
    def register(self, tool: Tool) -> None: ...
    def get(self, tool_name: str) -> Tool: ...

    def list_specs(
        self,
    ) -> tuple[ToolSpec, ...]: ...


class ToolDispatcher(Protocol):
    async def dispatch(
        self,
        call: ToolCall,
        *,
        tool: Tool,
        request: ToolExecutionRequest,
        dependencies: AgentContext,
    ) -> ToolHandlerResult: ...


class ResultNormalizer(Protocol):
    async def normalize(
        self,
        *,
        request: ToolExecutionRequest,
        call: ToolCall,
        spec: ToolSpec,
        artifact_store: "ResultArtifactStore",
        execution_fence: RunExecutionFence,
        started_at: datetime,
        finished_at: datetime,
        handler_result: ToolHandlerResult,
    ) -> "ToolNormalizationResult": ...


~~~

以上 Protocol 和 DTO 是 M4 的目标契约，不代表当前仓库已经存在这些类型。`run_ref` 是执行身份的规范来源，`run_id/user_id/conversation_id/thread_id` 是与之保持一致的扁平投影；M4 在构造请求时必须校验五个字段一致。`project_id` 和 `allowed_asset_ids` 由 M5 从当前运行状态复制，工具参数中的附件 ID 必须是 `allowed_asset_ids` 的子集；工具不能扩大项目或附件范围。AgentContext 仍是 app/agent/context.py 中的请求级外部依赖容器，不放进可序列化的 ToolExecutionRequest，不进入 Checkpoint 或 Planner Prompt。生产装配由 AgentService 注入依赖，工具对象不能自行创建数据库客户端、LLM 客户端、向量客户端或 Checkpointer。

`execution_fence` 由 M5 当前租约产生，不来自 Planner、客户端或工具参数。M4 在 `save_started`、外部调用前、Artifact/结果提交前分别确认 `owner_id/fencing_token` 仍是 `harness_runs` 的当前持有者；租约失效或出现更大 token 时返回 `ErrorCategory.CONFLICT`，旧 Worker 不得开始新的副作用或写完成结果。已经发往不可取消外部系统的调用仍按 indeterminate 处理，不能依靠 fence 宣称副作用已停止。

### 4.5 五类工具与现有代码衔接点

#### 4.5.1 data_catalog

**现有能力**：

- app/repositories/mysql/meta/mysql_meta_catalog_repository.py::MetaCatalogRepository 支持按 ID 读取表、字段、维度、关系、指标维度关系和维度值映射。
- app/repositories/qdrant/qa_meta_tables_repository.py、qa_meta_columns_repository.py、qa_meta_metrics_repository.py 和 qa_meta_dimension_values_repository.py 提供四类 Meta 语义检索。
- app/repositories/es/es_dimension_value_repository.py::DimensionValueSearch 提供维度值全文检索。

**需要新增**：DataCatalogTool 将表、字段、指标、维度、维度值和关系检索统一为一个高层工具；Planner 只看到 data_catalog，不看到四路低层召回节点。

**需要重构**：从 retrieve_columns、retrieve_tables、retrieve_metrics、retrieve_dimension_values 中提取只属于目录检索的组织逻辑，形成 DataCatalogService。旧 Query 图和新工具共享该 Service；节点保留 State 写入和流式事件职责，纯业务读取逻辑放入 Service。

**输出边界**：目录命中摘要进入 ToolResult.summary，可作为后续 SQL 依据的目录证据进入 evidence_refs；超过大小预算的完整候选列表进入 result_ref。目录结果不是企业知识库事实，也不自动写入长期 Memory。

#### 4.5.2 query_data

**目标工具名，不是现有函数**：当前仓库不存在独立 query_data()。真实可复用入口是 app/agent/query_graph.py::query_graph。

**现有内部链路**：

~~~text
extract_keywords
    -> columns/tables/metrics/dimension_values 并行召回
    -> merge_retrieved_info
    -> filter_metric/filter_table
    -> reconcile_filtered_context
    -> add_extra_context
    -> generate_sql
    -> execute_sql
    -> enrich_query_result
~~~

execute_sql 当前从 state.sql 读取 SQL，并通过 AgentContext.dw_repository.execute_query(sql) 返回 rows；enrich_query_result 保留原始 sql_result，同时生成 result_columns、display_sql_result、dimension_value_mappings 和 mapping_limitations。

**需要重构**：提取 QueryService，由旧 Query 图节点和 QueryDataTool 共同调用；QueryDataTool 不能通过伪造节点事件获得结果，也不能让 Planner 直接调用低层 SQL 节点。

**必须新增的 SQL 安全边界**：当前 DwRepository.execute_query() 没有显式只读 SQL 校验、statement timeout、结果上限和取消语义。Harness 路径必须在执行前拒绝写入、DDL、DCL、事务控制和多语句；数据库会话设置 statement timeout，服务层设置最大 rows。仅截断返回值而不限制数据库执行规模，不算完成结果上限保护。

**输出边界**：完整原始 rows、展示副本、result_columns、维度映射和 SQL 审计信息进入 ResultArtifactStore；ToolResult 只返回有界 summary、result_ref、必要 evidence_refs 和 limitations。原始值与展示名称的双轨规则沿用 enrich_query_result，不能用展示名称覆盖原始值。

#### 4.5.3 analyze_data

**目标工具名，不是现有函数**：当前仓库不存在独立 analyze_data()。当前能力分散在 plan_analysis、execute_analysis、query_graph 和 python_sandbox。

**现有能力**：execute_analysis 按 depends_on 分层，使用 asyncio.gather() 并行执行同层任务；每个任务调用 query_graph，基于真实 rows 生成 data_profile，调用 LLM 生成 calculate(rows)，再通过 execute_python_calculation() 执行，最后形成 TaskResult 和 AnalysisEvidence。

**需要重构**：提取 AnalysisService.plan() 和 AnalysisService.execute()。旧 plan_analysis 和 execute_analysis 节点保留现有流式事件兼容，但 Harness handler 使用 Service 的结构化返回，不直接依赖 Runtime.stream_writer。Service 必须显式接收用户、会话、线程和取消信号，不能借用全局 State。

**安全边界**：python_sandbox.py 当前明确是实验性隔离方案。M4 只能复用其受限调用，不把它升级为通用 Python 执行器；完整 python_code 不进入 Planner、Checkpoint 或 ToolResult.summary，只能进入受控结果存储并由引用回查。

**输出边界**：完整任务 rows、画像、计算结果和代码引用进入 result_ref；可用于结论的成功任务、字段语义和证据链进入 evidence_refs；失败任务、映射缺失和部分任务完成情况进入 limitations。AnalysisEvidence 中的 calculation_result 必须来自实际 Sandbox 返回值，不能由 LLM 填充。

#### 4.5.4 build_report

**目标工具名，不是现有函数**：当前仓库不存在独立 build_report()。现有能力由 generate_report_plan 和 render_report 两个节点组成。

**现有能力**：generate_report_plan 使用 build_report_context，只把受控任务上下文和最多 12 行预览数据交给 LLM；它只生成 ReportPlan，不绑定真实数值。render_report 读取真实 analysis_task_results 或单次查询结果，校验 ReportDataRef，最多展示 200 行并返回 RenderedReport。

**需要重构**：提取 ReportService.plan() 和 ReportService.render()。BuildReportTool 只允许引用当前 run 中已经存在的 source_task_ids，必须在服务端重新读取对应结果；不得接受模型直接提供的 rows、value、SQL 或组件数据。

**输出边界**：ReportPlan 和真实绑定后的 RenderedReport 保存到 result_ref；摘要只说明报告状态、绑定组件数量和关键限制。报告工具不能伪造指标、数值、任务 ID 或不存在的字段。渲染失败的组件必须保留 binding_status=failed 和 binding_error，并将整体结果归一化为 partial 或 unrecoverable_error。

#### 4.5.5 knowledge_base

**暂不启用**：当前没有独立企业知识库的仓储、索引、文档权限和更新治理。

MetaCatalogRepository、Qdrant Meta RAG 和 ES 维度值检索仍属于 Data Catalog / Meta RAG，只能解释指标、表、字段、维度和维度值；企业知识库应覆盖企业制度、业务规则、口径文档和分析规范，不能用目录检索冒充。

### 4.6 Tool Runtime 执行阶段

Runtime 的单次执行顺序固定为：

~~~text
M5 已提交 ToolCall
    -> 运行状态和阶段门禁
    -> Registry 名称与启用状态校验
    -> JSON Schema + Pydantic 参数校验
    -> 运行身份、超时和幂等门禁
    -> 幂等记录查询
    -> 计算有效超时
    -> ToolDispatcher 调用 handler
    -> ResultNormalizer stage Artifact 并归一化
    -> 同一业务事务提交 ToolResult、执行记录和 Artifact 可见状态
    -> 返回 ToolResult 给 Loop Controller
~~~

执行门禁和不变量：

1. state_status 不是 running、state_phase 不是 execute_tool、run_id 不匹配或 thread_id 不匹配时，不调用 handler，返回 conflict 或 unrecoverable_error。
2. tool_name 必须来自当前 Registry 版本且 enabled=True；不能通过大小写变化、别名或参数字段绕过名称校验。
3. 参数校验失败不生成 result_ref，不写业务数据，不消耗工具重试次数；错误交给 M5 作为受控工具错误处理。
4. 当前不执行用户级工具权限复核。运行身份校验只确认请求属于当前 `run_id`、`user_id`、`conversation_id` 和 `thread_id`，不能被描述为授权判断。
5. 有效超时为 `ToolCall.timeout_seconds`、`ToolSpec.timeout_seconds`、`RuntimePolicy.default_tool_timeout_seconds` 和 `RuntimePolicy.tool_timeout_ceiling_seconds` 按“调用方请求值可选、工具配置值、运行时默认值、运行时硬上限”计算的结果；任何结果都不能超过 `tool_timeout_ceiling_seconds`。
6. ToolRuntime 不负责循环重试。它只返回 `retryable` 和执行结果；M5 根据错误分类、action 幂等记录和运行预算决定是否重试。
7. handler 返回后必须先完成结果归一化和引用持久化，再向下游返回 `success` 或 `partial`；不能先发成功事件再异步保存结果。

### 4.7 ToolResult、错误分类和重试边界

M4 唯一对外结果是第 6.3 节定义的 ToolResult。字段语义如下：

| 字段 | 语义 | 进入哪里 | 约束 |
| --- | --- | --- | --- |
| tool_call_id | 当前工具调用身份 | M5 幂等记录、M1 观察 | 必须等于 ToolCall.action_id，不创建第二个动作身份 |
| tool_name | Registry 中的规范名称 | 观察、指标、审计 | 不接受 handler 自行改名 |
| status | 工具结果分类 | M5 续行判断 | 只能是 success、partial、temporary_error、needs_user、unrecoverable_error |
| summary | 有界、脱敏、面向下一次规划的摘要 | RunObservation.summary、ContextEngine | 不包含完整 rows、源码或堆栈 |
| result_ref | 完整结果或产物的不透明引用 | RunObservation.result_ref | 不直接包含 JSON rows；引用必须绑定 run/user 访问边界 |
| evidence_refs | 可用于结论追溯的证据引用集合 | RunObservation.evidence_refs | 只指向受控证据，不等于所有原始结果 |
| limitations | 截断、缺失映射、部分失败等限制 | 观察、最终收尾 | 必须可向用户解释，不写隐藏推理 |
| error_category/error_code/error_message | RunError 的扁平线投影 | M1 last_error、指标 | 三者同时存在或同时为空 |
| retryable | 当前结果是否允许 M5 考虑重试 | M5 重试策略 | 参数和状态错误默认不可重试 |
| started_at/finished_at/duration_ms | 工具执行时间信息 | 审计和指标 | 时间单调，duration_ms 不得为负 |

五类结果的归一化规则：

| 场景 | status | error_category | retryable | M5 后续动作 |
| --- | --- | --- | --- | --- |
| handler 成功且完整结果已保存 | success | 空 | False | 记录观察并重新 BuildContext |
| 结果可用但被截断、部分任务失败或部分组件绑定失败 | partial | 空 | False | 记录限制，重新规划是否补偿 |
| 依赖短暂不可用、连接短暂失败、可安全重试的超时 | temporary_error | tool、database 或 timeout | True | 按工具幂等策略有限重试 |
| 需要业务口径、时间范围或用户确认 | needs_user | user_input | False | 交给 M5 创建 ConfirmationRequest 并暂停 |
| 未注册、参数非法、只读校验失败、结果无法解释或不可安全重试 | unrecoverable_error | validation、conflict、tool 或 database | False | 由 M5 进入受控失败或最终收尾 |

状态与错误必须一致：success 和 partial 不携带错误三元组；temporary_error、needs_user 和 unrecoverable_error 必须同时携带 error_category、error_code 和 error_message。needs_user 必须携带不含 confirmation_id 的 AskUserRequest；确认 ID 由 M5 生成。ToolResult 不保留异常对象、完整 traceback、完整 raw output 或敏感权限上下文。

重试边界：

- 参数校验、未注册工具、禁用工具、SQL 只读校验失败和用户输入不足不重试。
- 读取型 data_catalog 和 query_data 在相同 ToolCall、相同参数摘要和相同数据版本下可安全重试；重试前先查询幂等记录，已成功的调用直接返回原 ToolResult。
- analyze_data 和 build_report 标记为 conditionally_idempotent。只有当输入摘要、源结果引用和数据版本未变，且没有外部写入副作用时才允许重试；否则重新规划，不自动重放。
- 超时后不能默认认为 handler 没有副作用。对 Query 和 Catalog，使用调用 ID 查询执行记录；无法确认结果是否提交时，返回冲突或人工确认，不盲目重放。
- M4 不递增 planner_retry_count、context_retry_count 或 tool_retry_counts；只返回 retryable 和执行记录，计数和预算归 M5。

### 4.8 ResultNormalizer 与结果持久化边界

ResultNormalizer 是 M4 的唯一结果出口。handler 返回的临时对象不能直接写入 Harness 状态、Checkpoint、SSE 或 API；必须先完成结构检查、脱敏、结果外置和证据引用生成。

处理顺序固定为：

1. 校验 payload 是否符合对应工具的结果模型，并验证可以安全 JSON 序列化；不可序列化结果归类为 unrecoverable_error。
2. 计算 payload 大小、行数、列数、摘要长度和限制项；超过内联预算的内容不得放入 summary。
3. 按 `run_id + tool_call_id + attempt + kind + ordinal + payload_hash` 生成确定性 artifact key，把完整 payload 和证据对象写为 `prepared` Artifact；prepared 记录不可被 API、Planner 或其他 run 读取。
4. 从真实 payload 提取可追溯的证据对象并同样 stage，得到待提交 `result_ref/evidence_refs`；证据必须关联来源任务、字段、查询或报告组件。
5. 对异常和限制做脱敏，形成 error_category、error_code、error_message 和 retryable；不保存异常对象、完整 traceback、连接串、权限 scope 或完整 Prompt。
6. 使用 UTC 时间和单调时钟计算 started_at、finished_at、duration_ms，构造第 6 节唯一的 ToolResult 草稿。
7. `ResultNormalizer` 接收装配层注入的 `ResultArtifactStore` 和当前 `execution_fence`，在返回 `ToolNormalizationResult` 前完成全部 `stage()`；不得从全局变量取 Store，也不得自行创建存储客户端。
8. `ToolExecutionStore.commit_finished()` 在同一 PostgreSQL 业务事务中验证所有 staged Artifact 的身份、hash、size、attempt 和 fencing token，写完成执行记录并把 Artifact 从 `prepared` 标记为 `committed`；事务成功后 ToolResult 才能返回下游。该事务不包含 LangGraph Saver，也不宣称跨数据库原子性。

存储边界如下：

| 数据 | Checkpointer | PostgreSQL | ResultArtifactStore | Planner Prompt |
| --- | --- | --- | --- | --- |
| ToolResult.summary | RunObservation 可保存 | 工具审计可保存 | 可重复保存 | 只传有界摘要 |
| result_ref | 只保存引用 | 执行记录保存引用 | 保存完整结果和产物 | 不展开原文 |
| evidence_refs | 观察引用可保存 | 证据索引可保存 | 保存受控证据 | 只传引用和摘要 |
| SQL rows | 不保存完整 rows | 不默认保存正文 | 按运行归属和保留策略保存 | 禁止直接传入 |
| python_code | 不保存源码 | 只保存审计引用 | 受控保存 | 禁止直接传入 |
| 原始事件和 traceback | 不保存 | 不保存原文 | 不保存或短期脱敏 | 禁止传入 |

当前仓库没有独立 ResultArtifactStore，也没有 ToolExecutionStore 或工具执行审计表。这些都是**需要新增**。在实现前必须确定存储介质、保留期、加密、访问控制、删除策略、结果版本和故障恢复策略。`result_ref` 不能实现成进程内字典键，也不能因为暂时没有 ArtifactStore 就把完整 rows 塞进 HarnessControlState。`harness_result_artifacts` 至少保存 `attempt`、`artifact_key`、`content_type`、`schema_version`、`status`、`orphaned_at`、`fencing_token`、`created_at`、`committed_at` 和受控审计字段；`harness_tool_executions` 至少保存 `fencing_token`、`input_digest`、`result_digest`、`prepared_artifact_keys`、`record_status`、`finished_at`、`indeterminate_at`、`error_code` 和 `updated_at`。

### 4.9 Tool Runtime Protocol 与装配边界

以下 Protocol 是目标接口，只描述边界，不实现外部调用：

~~~python
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator
from app.agent.harness.contracts import RunExecutionFence

from app.agent.harness.contracts import ContractModel, ToolCall, ToolResult, ToolSpec
from app.agent.harness.tools.contracts import ToolExecutionRequest


class ToolExecutionRecord(ContractModel):
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    input_digest: str = Field(min_length=64, max_length=64)
    attempt: int = Field(ge=0)
    record_status: Literal["started", "finished", "indeterminate"]
    fencing_token: int = Field(ge=1)
    result_digest: str | None = Field(default=None, min_length=64, max_length=64)
    prepared_artifact_keys: tuple[str, ...] = ()
    finished_at: datetime | None = None
    indeterminate_at: datetime | None = None
    error_code: str | None = None
    result: ToolResult | None = None

    @model_validator(mode="after")
    def validate_record_state(self) -> "ToolExecutionRecord":
        if self.record_status == "finished":
            if self.result is None or self.result_digest is None or self.finished_at is None:
                raise ValueError("finished 执行记录必须有结果、摘要和完成时间")
            if self.indeterminate_at is not None:
                raise ValueError("finished 执行记录不能同时标记 indeterminate")
        elif self.record_status == "indeterminate":
            if self.indeterminate_at is None or self.error_code is None:
                raise ValueError("indeterminate 执行记录必须有时间和错误码")
            if self.finished_at is not None:
                raise ValueError("indeterminate 执行记录不能有 finished_at")
        elif self.finished_at is not None or self.indeterminate_at is not None:
            raise ValueError("started 执行记录不能有结束时间")
        return self


class ToolExecutionStore(Protocol):
    async def get_latest_by_call_id(
        self,
        *,
        run_id: str,
        tool_call_id: str,
    ) -> ToolExecutionRecord | None: ...

    async def save_started(
        self,
        *,
        request: ToolExecutionRequest,
        input_digest: str,
        started_at: datetime,
        execution_fence: RunExecutionFence,
    ) -> Literal["started", "idempotent"]: ...

    async def mark_indeterminate(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        attempt: int,
        input_digest: str,
        execution_fence: RunExecutionFence,
        error_code: str,
    ) -> None: ...

    async def commit_finished(
        self,
        *,
        run_id: str,
        user_id: str,
        tool_call_id: str,
        result: ToolResult,
        input_digest: str,
        result_digest: str,
        attempt: int,
        staged_artifacts: tuple["ArtifactRecord", ...],
        expected_status: Literal["started"],
        execution_fence: RunExecutionFence,
    ) -> ToolResult: ...


class ArtifactWrite(ContractModel):
    artifact_key: str = Field(min_length=64, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    ordinal: int = Field(ge=0)
    payload: Any
    payload_hash: str = Field(min_length=64, max_length=64)
    content_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)
    schema_version: int = Field(ge=1)
    retention_until: datetime


class ArtifactRecord(ContractModel):
    ref: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    attempt: int = Field(ge=0)
    artifact_key: str = Field(min_length=64, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    ordinal: int = Field(ge=0)
    payload_hash: str = Field(min_length=64, max_length=64)
    content_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)
    schema_version: int = Field(ge=1)
    status: Literal["prepared", "committed", "orphaned", "deleted"]
    fencing_token: int = Field(ge=1)
    retention_until: datetime
    orphaned_at: datetime | None = None
    committed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_artifact_state(self) -> "ArtifactRecord":
        if self.status == "committed" and self.committed_at is None:
            raise ValueError("committed Artifact 必须有 committed_at")
        if self.status != "committed" and self.committed_at is not None:
            raise ValueError("非 committed Artifact 不能有 committed_at")
        if self.status == "orphaned" and self.orphaned_at is None:
            raise ValueError("orphaned Artifact 必须有 orphaned_at")
        if self.status != "orphaned" and self.orphaned_at is not None:
            raise ValueError("非 orphaned Artifact 不能有 orphaned_at")
        return self


class ToolNormalizationResult(ContractModel):
    """M4 内部结果；只有 commit_finished 返回的 ToolResult 可以跨模块。"""

    result: ToolResult
    staged_artifacts: tuple[ArtifactRecord, ...] = ()


class ResultArtifactStore(Protocol):
    async def stage(
        self,
        *,
        run_id: str,
        user_id: str,
        tool_call_id: str,
        attempt: int,
        artifact: ArtifactWrite,
        execution_fence: RunExecutionFence,
    ) -> ArtifactRecord: ...

    async def get(
        self,
        *,
        ref: str,
        run_id: str,
        user_id: str,
    ) -> Any: ...

    async def mark_orphaned(
        self,
        *,
        ref: str,
        run_id: str,
        tool_call_id: str,
        attempt: int,
        payload_hash: str,
        expected_status: Literal["prepared"],
        execution_fence: RunExecutionFence,
        reason: str,
    ) -> None: ...


class ToolRuntime(Protocol):
    async def execute(self, request: ToolExecutionRequest) -> ToolResult: ...
~~~

生产装配关系为：AgentService 注入 AgentContext、Registry、JsonSchemaValidator、ToolExecutionStore、ResultArtifactStore、Dispatcher、Normalizer 和 RuntimePolicy；ToolRuntime 编排这些依赖。当前不注入用户级工具授权服务。工具 handler 只依赖对应的 DataCatalogService、QueryService、AnalysisService 或 ReportService，不依赖 LoopController、PlanningAgent、ConversationRepository 或 MemoryManager。

M4 不创建新的 AgentContext。现有 app/agent/context.py::AgentContext 继续作为 LangGraph Runtime 的外部依赖容器；新增 Service 需要的依赖必须通过明确的装配参数提供，不能写入 AgentState，也不能在每次工具调用中创建新的客户端。

### 4.10 核心伪代码

~~~text
async execute(request):
    reject_if(request.state_status != running)
    reject_if(request.state_phase != execute_tool)
    reject_if(request.tool_call.action_id is empty)

    spec = registry.get(request.tool_call.tool_name)
    reject_if(spec.enabled is false)
    json_schema_validator.validate(
        instance=request.tool_call.arguments,
        schema=spec.input_schema,
    )
    typed_input = tool_input_model(spec.name).model_validate(
        request.tool_call.arguments
    )

    input_digest = digest(spec.name, spec.version, typed_input)
    record = await execution_store.get_latest_by_call_id(
        run_id=request.run_id,
        tool_call_id=request.tool_call.action_id,
    )
    if record is not None:
        reject_if_digest_conflicts(record, input_digest)
        reject_if(request.attempt < record.attempt)
        if request.attempt == record.attempt:
            if record.record_status == finished:
                return record.result
            return indeterminate_execution_conflict(record)
        reject_if(request.attempt != record.attempt + 1)
        reject_if(record.record_status != finished)
        reject_if(record.result.status != temporary_error)
        reject_if(not retry_allowed_by_spec(record, spec))

    started_at = utc_now()
    start_status = await execution_store.save_started(
        request=request,
        input_digest=input_digest,
        started_at=started_at,
        execution_fence=request.execution_fence,
    )
    if start_status == idempotent:
        return await reload_same_attempt_or_conflict(request, input_digest)
    timeout = effective_timeout(request.tool_call, spec, runtime_policy)
    try:
        raw = await wait_for(
            dispatcher.dispatch(
                request.tool_call,
                tool=registry.get(request.tool_call.tool_name),
                request=request,
                dependencies=agent_context,
            ),
            timeout=timeout,
        )
        normalized = await normalizer.normalize(
            request=request,
            call=request.tool_call,
            spec=spec,
            artifact_store=artifact_store,
            execution_fence=request.execution_fence,
            started_at=started_at,
            finished_at=utc_now(),
            handler_result=raw,
        )
    except KnownToolError as exc:
        normalized = without_artifacts(
            normalize_known_error(request.tool_call, spec, started_at, exc)
        )
    except TimeoutError as exc:
        normalized = without_artifacts(
            normalize_timeout(request.tool_call, spec, started_at, exc)
        )
    except UnknownToolError as exc:
        normalized = without_artifacts(
            normalize_unknown_error(request.tool_call, spec, started_at, exc)
        )

    await run_store.assert_execution_fence(
        request.run_ref, fence=request.execution_fence
    )
    return await execution_store.commit_finished(
        run_id=request.run_id,
        user_id=request.user_id,
        tool_call_id=request.tool_call.action_id,
        expected_status="started",
        result=normalized.result,
        input_digest=input_digest,
        result_digest=digest(normalized.result),
        attempt=request.attempt,
        staged_artifacts=normalized.staged_artifacts,
        execution_fence=request.execution_fence,
    )
~~~

上述 `reject_if`、`tool_input_model`、`digest`、`effective_timeout`、`retry_allowed_by_spec`、`reload_same_attempt_or_conflict`、`indeterminate_execution_conflict`、`without_artifacts`、`normalize_*` 和 `UnknownToolError` 是伪代码，不是当前仓库已有函数。`request.run_ref` 表示由请求身份字段构造的同一 `HarnessRunRef`，不是新增客户端输入。相同 `attempt` 的重复请求只能复用已完成结果；只有上一 attempt 明确完成为 `temporary_error`、下一 attempt 连续且 ToolSpec 允许时才再次调用 handler。需要特别处理 handler 已执行但进程在 `commit_finished` 前崩溃的场景：恢复时先查询 ToolExecutionStore；如果无法确认外部副作用是否完成，必须以 `mark_indeterminate()` 保留该 attempt，不得自动重放，应返回 conflict 或转入需要用户确认。工具成功事件只能在结果引用和执行记录提交成功后发出。

Artifact 的崩溃恢复和清理规则固定如下：

1. `stage()` 以 `(run_id, tool_call_id, attempt, kind, ordinal)` 作为不可复用的逻辑槽位，以 `artifact_key = sha256(run_id, tool_call_id, attempt, kind, ordinal, payload_hash)` 作为稳定引用键。同一槽位、同一 hash 返回原 `prepared/committed` 记录；同一槽位、不同 hash 返回 conflict，不能产生第二份结果。
2. `commit_finished()` 与 `harness_result_artifacts` 使用同一个 PostgreSQL 业务事务：先校验执行记录仍为当前 attempt 的 `started`、`input_digest/result_digest`、fencing token 未过期、ToolResult 中所有引用与 staged 记录完全一致，再写 `finished` 记录并把对应 Artifact 更新为 `committed`。任一校验失败时整笔业务事务回滚；该事务不包含 Saver checkpoint。
3. 进程在 stage 前崩溃时，不存在 Artifact；是否重试仍由工具幂等策略和执行记录决定。进程在 stage 后、commit 前崩溃时，Artifact 保持 `prepared` 且不可读，执行记录转为或保持 `indeterminate`，恢复流程不得仅凭 Artifact 存在就重放 handler。
4. commit 成功但响应丢失时，重复请求通过执行记录读取原 ToolResult；`commit_finished()` 对相同 input/result digest 返回原结果，对不同 digest 返回 conflict。
5. 后台维护任务只清理超过保留宽限期、没有匹配 `finished` 执行记录且执行 lease 已过期的 `prepared` Artifact。它先以 hash 和状态条件标记 `orphaned`，经过审计保留期后再标记 `deleted` 并删除正文；不能物理删除 `committed` Artifact。
6. `ResultArtifactStore.get()` 只返回 `status=committed` 且同时匹配 `ref + run_id + user_id` 的记录；`prepared`、`orphaned`、`deleted` 均按不可见处理。当前不校验用户权限，`user_id` 只用于运行归属和结果隔离。

### 4.11 字段级数据流

| 上游模块 | 上游字段 | 当前模块如何消费 | 当前模块输出字段 | 下游模块 | 下游用途 |
| --- | --- | --- | --- | --- | --- |
| M5 Loop Controller | NextAction.tool_call | 只接收已提交动作 | ToolExecutionRequest.tool_call | ToolRuntime | 选择并执行高层工具 |
| M1 Harness 状态 | run_id、user_id、conversation_id、thread_id | 组成执行身份并校验归属 | ToolExecutionRequest 身份字段 | Registry、ArtifactStore、ExecutionStore | 幂等和结果访问边界 |
| M1 Harness 状态 | status、phase、`tool_retry_counts[action_id]` | 校验是否允许执行并确定 attempt | state_status、state_phase、attempt | ToolRuntime | 只允许 running/execute_tool；同 attempt 不重复 handler |
| M1 运行身份 | run_id、user_id、conversation_id、thread_id | 执行前确认同一运行和结果隔离边界 | 身份一致性判定 | ToolRuntime、ArtifactStore | 防止跨运行读取或写入 |
| ToolRegistry | ToolSpec.name、version、enabled | 名称查找和启用检查 | 规范 ToolSpec | Planner、SchemaValidator、Runtime | 统一能力快照和执行规则 |
| ToolRegistry | ToolSpec.input_schema | 校验 arguments | typed tool input | 对应 Tool handler | 防止参数越界和额外字段 |
| M4 输入 DTO | DataCatalogInput | 驱动 Meta/Qdrant/ES 读取 | ToolHandlerResult.payload | ResultNormalizer | 目录摘要、证据和候选结果 |
| M4 输入 DTO | QueryDataInput.question、asset_ids、max_rows | 传给 QueryService | query payload | ResultNormalizer | rows、列语义和限制 |
| M4 输入 DTO | AnalyzeDataInput.question、analysis_goals | 传给 AnalysisService | analysis payload | ResultNormalizer | TaskResult、AnalysisEvidence 和限制 |
| M4 输入 DTO | BuildReportInput.source_task_ids | 读取当前 run 已有结果 | report payload | ResultNormalizer | ReportPlan 和 RenderedReport |
| Tool handler | ToolHandlerResult.payload | 检查大小、结构和序列化，按逻辑槽位 stage | `ToolNormalizationResult.staged_artifacts` | ToolExecutionStore | 与 ToolResult 一起提交后生成可读 result_ref |
| ResultNormalizer | `ToolResult` 草稿、prepared Artifact | 校验引用、hash、size 和当前 fence | `commit_finished()` 返回的 ToolResult | M5 Loop Controller | 只有 committed Artifact 的引用可进入观察 |
| Tool handler | evidence_payloads | 保存受控证据 | ToolResult.evidence_refs | M1 RunObservation | 下次 ContextEngine 和最终答案追溯 |
| Tool handler | summary、limitations | 脱敏和截断 | ToolResult.summary、limitations | M1/M2/M3 | 运行态观察和下一次规划 |
| Tool handler 异常 | 异常类型和安全状态 | 转换为稳定错误分类 | error_category、error_code、error_message、retryable | M5 Loop Controller | 重试、暂停或失败分支 |
| ToolRuntime | ToolResult | 保存执行结果并返回 | ToolResult | M5 Loop Controller | 记录观察、重新 build 或暂停 |
| ToolResult | tool_call_id | 映射回原动作身份 | RunObservation.action_id | M1 Harness 状态 | 保持动作与结果一一对应 |
| ToolResult | result_ref、evidence_refs | 只复制受控引用 | RunObservation.result_ref、evidence_refs | M2 ContextEngine | 下一次 build 的新证据 |

闭环字段流固定为：

~~~text
NextAction.tool_call
    -> ToolExecutionRequest
    -> Registry/Schema/State/Identity 门禁
    -> ToolDispatcher
    -> ToolHandlerResult
    -> ResultArtifactStore.stage(prepared)
    -> ToolExecutionStore.commit_finished(committed)
    -> ToolResult
    -> RunObservation
    -> RuntimeContext
    -> CompiledContext
~~~

ToolResult 到 RunObservation 的映射由 M5 负责写入状态：tool_call_id 映射为 action_id，tool_name/status/summary/result_ref/evidence_refs/limitations 原样受控复制，错误三元组映射为统一 RunError。M4 不直接修改 HarnessControlState。

### 4.12 与现有代码的衔接和文件级任务

| 任务 | 类型 | 文件 | 产出 | 前置 |
| --- | --- | --- | --- | --- |
| M4.1 | 需要重构 | app/agent/harness/contracts.py | 补齐第 6.3 节唯一 ToolSpec、ToolCall、ToolResult、RunObservation 和 ActionType 契约 | M1/M3 |
| M4.2 | 需要新增 | app/agent/harness/tools/contracts.py | 工具输入 DTO、ToolExecutionRequest、ToolHandlerResult、RuntimePolicy | M4.1 |
| M4.3 | 需要新增 | app/agent/harness/tools/registry.py | 名称唯一、版本、启用状态和 list_specs | M4.2 |
| M4.4 | 需要新增 | app/agent/harness/tools/runtime.py | 门禁、超时、幂等查询、Dispatcher 和结果保存编排 | M4.2/M4.3 |
| M4.5 | 需要新增 | app/agent/harness/tools/normalizer.py | 大对象外置、证据引用、错误归一化和 ToolResult 构造 | M4.2 |
| M4.6 | 暂不新增 | app/agent/harness/tools/permission.py | 用户级工具授权不在本期；未来认证授权方案确定后再单独设计 | 应用认证方案 |
| M4.7 | 需要新增 | app/agent/harness/tools/storage.py | ToolExecutionStore、ResultArtifactStore 的正式端口与实现 | 存储方案 |
| M4.8 | 需要新增/需要重构 | app/services/data_catalog_service.py | 复用 Meta/Qdrant/ES 的高层目录查询 | M4.2 |
| M4.9 | 需要新增/需要重构 | app/services/query_service.py | 从 query_graph 和节点提取共享问数逻辑，保留旧图兼容 | M4.2 |
| M4.10 | 需要新增/需要重构 | app/services/analysis_service.py | 从 execute_analysis 提取任务执行和证据形成，保留旧流式节点 | M4.9 |
| M4.11 | 需要新增/需要重构 | app/services/report_service.py | 从报告规划与渲染节点提取 plan/render，绑定真实数据 | M4.9/M4.10 |
| M4.12 | 需要重构 | app/repositories/dw_repository.py | 只读校验、statement timeout、结果上限、取消和错误分类 | M4.9 |
| M4.13 | 需要新增 | tests/test_harness_tools.py | DTO、Registry、运行身份、门禁、超时、幂等和归一化单测 | M4.1-M4.7 |
| M4.14 | 需要新增 | tests/test_tool_runtime_integration.py | Catalog/Query/Analysis/Report 与真实依赖的集成测试 | M4.8-M4.12 |

**现有能力，过渡期直接复用**：app/agent/query_graph.py、app/agent/nodes/execute_analysis.py、app/agent/nodes/generate_report_plan.py、app/agent/nodes/render_report.py 和 app/agent/python_sandbox.py。过渡复用不等于它们已经符合 Tool Protocol；Service 提取后，旧节点保留状态映射和流式事件，新 Harness 工具调用共享 Service。

M4 不创建 DataCatalogAdapter、QueryAdapter、AnalysisAdapter 或 ReportAdapter。只有存在稳定的协议转换时才建立明确端口；如果只是把字典复制到另一个字典，应直接提取共享 Service 或删除转换层。

### 4.13 测试与验收标准

**DTO 与 Registry 单元测试**：

- ToolSpec 名称唯一、版本非空、enabled、timeout、input_schema、idempotency 和 result_kind 校验通过；重复注册、禁用工具和未知工具被拒绝。
- DataCatalogInput、QueryDataInput、AnalyzeDataInput、BuildReportInput 对空字符串、额外字段、超长文本、ID 数量、任务数量、rows 上限和组件类型进行边界校验。
- ToolCall.action_id 与 ToolResult.tool_call_id 同值；ToolResult 的 status、错误三元组、confirmation_request、retryable、时间字段和引用字段组合非法时被拒绝。
- Registry.list_specs() 只返回已注册且启用的工具；不接受模型或调用方传入的额外工具，不把 `permission` 字段解释为当前授权结果。

**执行门禁与错误测试**：

- running/execute_tool 以外状态不调用 handler；waiting_confirmation、completed、failed、cancelled 和 timeout 均不能执行工具。
- 未注册、禁用、参数错误、Schema 错误、状态/阶段冲突和运行身份不一致都在 handler 之前失败，并且没有业务副作用、没有结果引用、没有成功事件。
- 当前不执行用户级工具权限复核；`user_id`、`conversation_id`、`thread_id` 和 `run_id` 只用于运行归属和结果隔离。
- handler 超时、数据库短暂不可用和可重试运输错误映射为 `temporary_error`；参数、只读校验和结果结构错误映射为 `unrecoverable_error`。
- needs_user 结果只能由工具业务规则触发，必须携带 AskUserRequest，但不得携带 M5 生成的 confirmation_id。

**结果与存储测试**：

- 小结果可以只保存受控摘要；大结果必须保存到 ResultArtifactStore 并返回 result_ref。Planner Prompt 不出现完整 rows、源码、异常堆栈或完整事件。
- evidence_refs 能回查到来源任务、字段、查询或报告组件；无来源的模型文本不能直接变成证据引用。
- SQL rows 保留原始值，display_sql_result 只作为展示副本；报告组件只能绑定当前 run 已存在的结果引用。
- ToolExecutionStore 对相同 `run_id + tool_call_id + attempt` 返回相同结果；相同输入重复 attempt 幂等成功，输入摘要冲突或 attempt 跳号返回 conflict，不覆盖原记录。只有前一 attempt 明确为可重试 `temporary_error` 时才允许连续的下一 attempt。
- handler 执行后进程崩溃、结果保存失败、取消和超时均有明确记录状态；不能出现“已发成功事件但结果未保存”的状态。

**真实能力集成测试**：

- Data Catalog 覆盖 MetaCatalogRepository、Qdrant 四类语义仓储和 DimensionValueSearch；不能把 Meta RAG 误标记为 Enterprise Knowledge Base。
- Query Data 覆盖 query_graph 全链路、真实 SQL rows、结果增强、行数上限和 SQL 只读边界；query_data 作为目标工具名，不把它写成当前已有函数。
- Analyze Data 覆盖依赖分层、同层并行、Query Agent、真实 rows、Sandbox 计算、TaskResult 和 AnalysisEvidence；失败任务能形成 partial 或失败结果。
- Build Report 覆盖 ReportPlan、ReportDataRef 校验、真实结果绑定、200 行展示上限和 RenderedReport 的组件失败状态；不得伪造 value。
- knowledge_base 不出现在 Planner 的工具列表，也不能通过直接构造 ToolCall 绕过 Registry 执行。

**M4 验收标准**：

1. Planning Agent 只能从 Registry 获得高层 ToolSpec；Tool Runtime 是唯一业务工具执行入口，Planner 不访问任何业务仓储。
2. 五类工具分别完成启用状态、职责边界和真实代码衔接说明；query_data、analyze_data、build_report 均明确为目标名称而非当前已有函数；knowledge_base 保持暂不启用。
3. 统一 ToolResult 覆盖规则要求的身份、状态、摘要、结果引用、证据引用、限制、错误、重试和时间字段，并能稳定映射为 M1 RunObservation。
4. 参数、状态、阶段、运行身份、超时和幂等门禁在 handler 之前或结果保存之前生效；用户级权限不在本期执行链路内。
5. 完整 rows、Python 源码、大段报告和原始事件不进入 Harness 状态或 Planner Prompt；均通过受控结果存储和引用传递。
6. 安全可重试工具与有条件可重试工具边界清晰；同一成功动作不会因恢复或重复请求再次执行；不确定副作用时不盲目重放。
7. M4 单元测试和集成测试通过后，才进入 M5；M4 不实现 Loop Controller、暂停恢复 API、Finalization 或 Memory Formation。

**完善判定**：M4 的职责、输入/输出 DTO、Registry、Tool Protocol、五类工具衔接、执行门禁、错误与重试、结果外置、幂等、字段流、文件任务和测试均已覆盖，文档层面判定为完善。代码层面仍属于需要新增/重构，必须完成 M4.1～M4.14 并通过测试后才视为实现完成。

## 11. Loop Controller

> 架构模块 5；本章小节沿用模块内编号，研发阶段编号见第 21 节。

> 本节是 M5 的文档设计基线。M5 是 Harness 的唯一调度中心，只编排 M1～M4 和 M6 的正式接口，不接管 ContextEngine、Planning Agent、工具 handler 或 Memory Formation 的内部逻辑。

### 6.1 模块职责

**一句话职责**：创建或恢复同一个 Harness Run，按状态机驱动 BuildContext、Plan、动作提交、工具执行、观察记录、暂停恢复和 Finalization，直到返回暂停结果或终态结果。

**现有能力**：

- `app/services/agent_service.py::_run_async()` 已能创建运行身份、调用已编译 LangGraph、保存轮次并返回同步结果；`qyStream()` 已能消费 `stream_mode=["custom", "values"]` 并输出带心跳的 SSE。
- `app/clients/postgres_client.py` 已创建和 setup 同一个 `AsyncPostgresSaver`，并把它传给现有 `build_agent_graph(checkpointer=...)`。
- `app/repositories/conversation_repository.py::start_turn()` 已在一个事务中创建 turn 和用户消息，并设置 `conversations.active_run_id`。
- `app/agent/graph.py` 已展示当前项目的 `StateGraph(state_schema=AgentState, context_schema=AgentContext)` 装配方式，但没有 Harness 级回边、暂停或恢复。

**需要新增**：

- `LoopController`、`build_harness_graph()`、`HarnessRunner`、`LoopPolicy`、统一运行事件和运行协调存储。
- 同一会话单活 run 的条件写入、动作提交协调、确认记录、取消标记和运行状态查询。
- 恢复入口，能够加载同一 `thread_id` 的 checkpoint，并保留原 `turn_id`、`run_id`、目标、观察和业务现场。

**需要重构**：

- `AgentService._run_async()` 与 `qyStream()` 只负责把 API 请求映射为 Harness 命令，并消费同一个 `LoopController`；不再各自实现保存、错误收尾和 Memory Formation。
- `ConversationRepository.start_turn()` 必须在设置 `active_run_id` 前拒绝另一个非终态 run；暂停时保持 `active_run_id`，不能调用 `finish_turn()`。
- M3 `ActionCommitter` 的“原子”语义改为可恢复协调：当前 `AsyncPostgresSaver` 没有暴露与业务表共享事务的接口，不能虚构跨 Saver 和业务表的单事务。实现采用 prepared -> checkpoint -> committed 可见状态，并在恢复时对 prepared 记录做对账；M4 只能消费 committed 动作。

**不负责**：生成上下文内容、决定下一步动作、执行业务工具、保存完整工具结果、构造最终会话输出、提取长期记忆、直接调用 `MemoryManager.add()`、实现用户级工具权限或保存完整 Prompt/reasoning。

### 6.2 运行状态与控制步骤

规则要求的控制步骤映射到现有统一枚举如下：

| 控制步骤 | `HarnessStatus` / `LoopPhase` | 所有者 | 持久化要求 |
| --- | --- | --- | --- |
| StartRun | `running/start_run` | Loop Controller | 创建 turn、run 业务记录和初始 checkpoint |
| RestoreRun | `running/restore_run` | Loop Controller | 进程中断恢复；完整身份、版本和 prepared 动作对账后保存，不处理用户回复 |
| BuildContext | `running/build_context` | M5 调 M2 | 只保存 build_id、token_count 和受控错误，不保存 CompiledContext 正文 |
| Plan | `running/plan` | M5 调 M3 | Planner 草稿不持久化；成功后进入动作提交 |
| ValidateAction | `running/validate_action` | M3 校验，M5 提交 | prepared 动作与 checkpoint 协调后才可见 |
| ExecuteTool | `running/execute_tool` | M5 调 M4 | 调 handler 前必须已有 committed 动作和 execute_tool checkpoint |
| HandleToolResult | `running/handle_tool_result` | Loop Controller | 按结果类型决定重试、暂停、重规划或失败 |
| RecordObservation | `running/record_observation` | Loop Controller | ToolResult 映射为 RunObservation 后保存 checkpoint |
| CheckContinuation | `record_observation` 末尾的确定性决策 | Loop Controller | 不新增同义枚举；决定下一阶段后保存 |
| PauseRun | `waiting_confirmation/wait_confirmation` | Loop Controller | 先保存确认记录和 checkpoint，再返回 API/SSE |
| ResumeRun | `running/restore_run` | Loop Controller | 用户确认恢复；先解析并核验 `ConfirmationReply`，再合并白名单条件并重新 BuildContext |
| Finalization | `running/finalization` | M5 调 M6 | 先保存 terminal_intent；M6 成功后才进入终态 |

`CheckContinuation`、`PauseRun` 和 `ResumeRun` 是控制步骤，不另造 `LoopPhase` 同义值。M1 已冻结的 `record_observation`、`wait_confirmation` 和 `restore_run` 分别承载这三个步骤。`RestoreRun` 与 `ResumeRun` 共享 `restore_run` 阶段但不是同一命令：前者由进程/任务恢复器调用，只恢复未完成的运行现场；后者由用户确认接口调用，必须携带并消费 `ConfirmationReply`。二者都不得创建新 `run_id`、`turn_id` 或 `thread_id`。

### 6.3 输入 DTO 与校验

API 不直接向 Loop Controller 传裸字典。新运行先由 `AgentService._new_identity()` 生成可信身份；当前代码固定 `thread_id == conversation_id`。恢复和取消使用 M1 唯一的 `HarnessRunRef`，其中 `user_id` 来自服务端当前身份；当前项目尚无真实登录体系，默认用户只用于运行归属校验，不等于用户工具授权。

~~~python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from app.agent.harness.contracts import (
    ConfirmationReply,
    ConfirmationRequest,
    ConfirmationRecord,
    ContractModel,
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
    RunError,
)
from app.agent.harness.finalization import FinalizationResult


class LoopPolicy(ContractModel):
    max_iterations: int = Field(default=8, ge=1, le=64)
    planner_max_retries: int = Field(default=2, ge=0, le=8)
    context_max_retries: int = Field(default=2, ge=0, le=8)
    tool_max_retries: int = Field(default=2, ge=0, le=8)
    run_timeout_seconds: int = Field(default=300, ge=1, le=3_600)
    confirmation_ttl_seconds: int = Field(default=86_400, ge=60)


class StartRunCommand(ContractModel):
    run_ref: HarnessRunRef
    input_text: str = Field(min_length=1, max_length=20_000)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)


class RestoreRunCommand(ContractModel):
    """进程中断或服务重启后的恢复，不携带用户确认回复。"""

    run_ref: HarnessRunRef


class ResumeRunCommand(ContractModel):
    run_ref: HarnessRunRef
    reply: ConfirmationReply


class CancelRunCommand(ContractModel):
    run_ref: HarnessRunRef
    # API 的 Idempotency-Key 在服务边界映射为该字段。
    cancel_request_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="user_cancelled", min_length=1, max_length=500)


class HarnessEvent(ContractModel):
    event_id: str = Field(min_length=1)
    event_type: Literal[
        "run.started",
        "context.built",
        "planner.completed",
        "tool.started",
        "tool.completed",
        "run.paused",
        "run.resumed",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.timeout",
    ]
    run_ref: HarnessRunRef
    phase: LoopPhase
    status: HarnessStatus
    iteration: int = Field(ge=0)
    action_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    emitted_at: datetime


class RunningRunSnapshot(ContractModel):
    """只读状态查询结果；不表示一次同步执行已经完成。"""

    run_ref: HarnessRunRef
    status: Literal[HarnessStatus.RUNNING] = HarnessStatus.RUNNING
    phase: LoopPhase
    iteration: int = Field(ge=0)
    action_seq: int = Field(ge=0)
    cancel_requested: bool = False
    lease_expires_at: datetime | None = None
    last_error: RunError | None = None
    updated_at: datetime


class CancelAccepted(ContractModel):
    """取消标记已持久化，但尚未完成 Finalization。"""

    cancel_request_id: str = Field(min_length=1)
    run_ref: HarnessRunRef
    accepted: Literal[True] = True
    status: Literal[HarnessStatus.RUNNING, HarnessStatus.WAITING_CONFIRMATION]
    phase: LoopPhase
    iteration: int = Field(ge=0)
    cancel_requested: Literal[True] = True
    requested_at: datetime


class LoopRunResult(ContractModel):
    run_ref: HarnessRunRef
    status: HarnessStatus
    phase: LoopPhase
    iteration: int = Field(ge=0)
    pending_confirmation: ConfirmationRequest | None = None
    finalization_result: FinalizationResult | None = None
    last_error: RunError | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "LoopRunResult":
        if self.status is HarnessStatus.WAITING_CONFIRMATION:
            if self.phase is not LoopPhase.WAIT_CONFIRMATION:
                raise ValueError("waiting_confirmation 结果的 phase 必须为 wait_confirmation")
            if self.pending_confirmation is None or self.finalization_result is not None:
                raise ValueError("暂停结果必须且只能携带 pending_confirmation")
        elif self.status in {
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
            HarnessStatus.TIMEOUT,
        }:
            if self.phase is not LoopPhase.FINALIZATION:
                raise ValueError("终态结果的 phase 必须为 finalization")
            if self.finalization_result is None or self.pending_confirmation is not None:
                raise ValueError("终态结果必须且只能携带 finalization_result")
            if self.finalization_result.run_ref != self.run_ref:
                raise ValueError("LoopRunResult 与 FinalizationResult 的 run_ref 必须一致")
            if self.finalization_result.status is not self.status:
                raise ValueError("LoopRunResult 与 FinalizationResult 的 status 必须一致")
        else:
            raise ValueError("LoopRunResult 只能表示暂停或终态")
        return self
~~~

`LoopPolicy` 的数值是建议默认值，不是当前已验证配置。实现时在 `app/core/config.py` 和项目现有配置文件中增加 `harness` 配置段，由应用装配注入；Planner、工具参数和 API 请求均不能覆盖硬上限。

输入校验规则：

1. 新运行的 `run_ref` 五个身份字段必须与 `AgentService` 本次生成值一致；`thread_id` 必须等于当前 `conversation_id`。
2. `ConversationRepository.start_turn()` 以会话行为条件锁；`active_run_id` 为空才能创建新 run，相同 `run_id` 仅允许幂等返回，不同非终态 run 返回 conflict。
3. 恢复必须同时校验 checkpoint、`harness_runs`、`conversation_turns` 和 `conversations.active_run_id` 中的 `user_id/conversation_id/thread_id/turn_id/run_id`。
4. `ConfirmationReply.confirmation_id` 必须命中当前唯一 `status=pending` 且 `visibility=published`、未过期的确认；`decision="confirm"` 才能合并白名单条件，`decision="reject"` 不合并条件并进入取消收尾；重复提交相同 payload 幂等成功，不同 payload 返回 conflict。
5. `resolved_conditions` 只接受 `ConfirmationRequest.required_fields` 允许的键，并经对应业务类型校验；不能把客户端任意字典直接合并进 Planner 上下文。
6. 终态 run 不可 restore/resume；`RestoreRunCommand` 只接受 `running` 或 `running/finalization`，不要求 `ConfirmationReply`；`ResumeRunCommand` 只接受 `waiting_confirmation`，且必须消费当前 published、未过期的确认。

### 6.4 输出、事件与持久化边界

`LoopRunResult` 只表示“暂停”或“终态”，不会把中间 `running` 状态作为同步调用完成结果。`RunningRunSnapshot` 只用于状态查询或执行权已被其他 Worker 持有时的只读响应；`CancelAccepted` 只表示取消标记已持久化，不表示运行已经进入 `cancelled`。同步 API 等待暂停或终态；SSE 使用同一控制流实时发送事件，最后仍以相同 `LoopRunResult` 收口。

| 输出 | 下游 | Checkpointer | PostgreSQL | 限制 |
| --- | --- | --- | --- | --- |
| `RunObservation` | M2 RuntimeContext、M3 PlannerStateView | 保存摘要和引用 | 工具执行审计保存引用 | 不保存完整 rows、源码或报告正文 |
| `ConfirmationRequest` | API/SSE、恢复入口 | `pending_confirmation` | `harness_confirmations.status=pending`、`visibility=prepared|published` | 只允许一个 pending；仅 published 对 API 可见；不能写为 assistant 最终消息 |
| `FinalizationInput` | M6 | 不作为长期状态重复保存 | finalization 协调记录 | 只携带受控答案、引用、限制和错误 |
| `LoopRunResult` | AgentService/API/SSE | 不单独保存 | 可由 run/turn 状态重建 | 暂停和终态 payload 互斥 |
| `RunningRunSnapshot` | 状态查询/API | 不保存 | 从 `harness_runs` 重建 | 只读；不得触发 restore 或推进状态 |
| `CancelAccepted` | 取消 API | 不保存 | `cancel_requested` 和取消请求 ID 已持久化 | 只表示已受理；最终结果仍由 `LoopRunResult` 表达 |
| `HarnessEvent` | SSE/Tracing | 不进入状态 | 只保存压缩 trace 或可观测系统 | 不包含 Prompt、SQL rows、附件正文或隐藏 reasoning |

统一事件最少包括 `run.started`、`context.built`、`planner.completed`、`tool.started`、`tool.completed`、`run.paused`、`run.resumed`、`run.completed` 和 `run.failed`；取消和超时分别使用 `run.cancelled`、`run.timeout`。事件由 M5 的 EventSink 产生一次，SSE 只是订阅者，不能再次写会话历史。

### 6.5 主循环与三类动作分派

每次成功调用 Planning Agent 计为一次 iteration；Planner 自身结构化输出重试不增加 iteration。成功或部分成功工具结果必须先写入观察，再重新 BuildContext，因为工具结果是下一次规划的新证据。

~~~text
StartRun / RestoreRun
    -> BuildContext
    -> Plan
    -> ValidateAction + CommitAction
    -> tool_call
         -> ExecuteTool
         -> HandleToolResult
         -> RecordObservation
         -> CheckContinuation
         -> BuildContext
    -> ask_user
         -> Create ConfirmationRequest
         -> PauseRun + Checkpoint
         -> waiting_confirmation
    -> final_answer
         -> terminal_intent=completed
         -> Finalization
~~~

动作提交采用如下可恢复协调，不声称与 LangGraph Saver 共享数据库事务：

1. `ActionCommitter` 对 `run_id + action_seq` 条件写入 prepared 动作，保存受控 payload、摘要和 digest；同键同 digest 幂等，不同 digest 冲突。
2. Loop Controller 把 `action_seq`、`phase` 和动作引用写入同一线程 checkpoint。
3. `ActionCommitter` 将 prepared 动作标记 committed。只有 committed 动作可进入 M4、确认流程或 M6。
4. 进程在第 1～3 步间中断时，恢复流程比较业务记录和 checkpoint：无 checkpoint 的 prepared 记录不可见并可回滚；checkpoint 已包含同一 digest 时补记 committed；任何 digest 不一致都进入 conflict，不自动执行。
5. 工具动作进入 M4 前再次查询 `ToolExecutionStore`；已有成功结果直接返回并转为观察，不能重放 handler。

### 6.6 重试、重新规划和续行策略

同一动作重试与重新规划是两条不同路径：重试保持同一个 `action_id` 和参数 digest；重新规划重新 BuildContext 并生成新的 `action_seq/action_id`。不能通过“重新规划同样参数”绕开重试上限。

| 场景 | 计数 | 是否同一输入 | 耗尽后的处理 |
| --- | --- | --- | --- |
| Context 临时依赖失败 | `context_retry_count += 1` | 同一 ContextRequest | 写受控 RunError；可重新规划时进入 Plan，否则失败收尾 |
| Context 校验、身份或附件归属错误 | 不重试 | 否 | 直接失败收尾 |
| Planner 解析/临时运输失败 | `planner_retry_count += 1` | 同一 CompiledContext，附受控错误反馈 | 可选择 ask_user 的场景暂停，否则失败收尾 |
| Tool temporary_error 且 `retryable=True` | `tool_retry_counts[action_id] += 1` | 同一 ToolCall、同一 digest | 记录失败观察并重新规划；不可确认副作用时暂停或 conflict |
| Tool needs_user | 不重试 | 否 | 创建确认请求并暂停 |
| Tool partial/success | 不重试 | 否 | 记录观察，重置 context/planner 当前重试计数，重新 BuildContext |
| Tool unrecoverable_error | 不重试 | 否 | 记录观察；允许 Planner 基于错误补偿一次，无法补偿则失败收尾 |

`planner_retry_count` 在成功产出并提交动作后清零；`context_retry_count` 在成功 build 后清零。`tool_retry_counts` 按 `action_id` 计数，新工具动作天然从 0 开始；旧计数作为本 run 的有界审计保留，不迁移到新 action。M5 不在 M4 内部循环调用重试，也不重试 `needs_user`。

达到 `max_iterations` 时写 `RunError(code="max_iterations_exceeded")` 并进入 `failed` intent；达到 `deadline_at` 时发出取消信号并进入 `timeout` intent。每个外部调用前后都检查取消与 deadline，运行超时不能只依赖单个工具超时。

### 6.7 暂停、确认与恢复

暂停来源只有已提交的 `ask_user` 或 M4 `ToolResult(status=needs_user)`。后者的 `AskUserRequest` 先转换为正式 `ConfirmationRequest`；`confirmation_id` 由服务端生成，不接受 Planner 或工具提供。

~~~text
ask_user / ToolResult.needs_user
    -> ConfirmationStore.prepare(status=pending, visibility=prepared, digest)
    -> state.pending_confirmation = ConfirmationRequest
    -> status=waiting_confirmation, phase=wait_confirmation
    -> save checkpoint
    -> ConfirmationStore.publish(confirmation_id, digest)
       status 仍为 pending，visibility: prepared -> published
    -> emit run.paused
    -> API/SSE 返回

ConfirmationReply
    -> 校验可信运行身份、active_run_id、confirmation_id 和 expires_at
    -> 条件更新 confirmation: status pending -> confirmed/rejected
       visibility 保持 published
    -> confirmed:
         -> 合并白名单 resolved_conditions
         -> 清除 pending_confirmation
         -> status=running, phase=restore_run
         -> save checkpoint and emit run.resumed
         -> BuildContext
    -> rejected:
         -> 不合并 resolved_conditions
         -> 清除 pending_confirmation
         -> status=running, phase=finalization
         -> terminal_intent=cancelled
         -> save checkpoint
         -> M6 Finalization
~~~

`run_id`、`turn_id`、`thread_id` 在暂停恢复期间都保持不变。当前 `thread_id == conversation_id`，不能在 resume 时创建新 thread。确认被拒绝映射为 `terminal_intent=cancelled`；确认过期返回 conflict，不修改 checkpoint；重复确认同一 payload 返回当前结果。暂停不是完成：`conversation_turns.completed_at` 保持空，`conversations.active_run_id` 保持当前 run，不写 AI 最终消息、不创建 `turn_outputs`、不触发 Memory Formation。

口径冲突、时间范围缺失、历史结果或附件引用不唯一、工具需要业务确认均使用同一流程。用户确认的条件只进入当前 run 的 `resolved_conditions`，不能未经 Memory Governance 修改全局指标定义或长期记忆。

### 6.8 取消、超时和崩溃恢复

- 取消请求以 `cancel_request_id + run_id` 为幂等键，对 `harness_runs` 条件设置 `cancel_requested=True`，不新增额外控制状态。运行仍为 `running` 时立即返回 `CancelAccepted`；当前 owner 在下一个安全点停止 Planner/工具，并以 `terminal_intent=cancelled` 进入 M6，完成后状态查询返回终态 `LoopRunResult`。
- 对 `waiting_confirmation` 取消时，取消请求先以同一事务把 pending confirmation 标记为 rejected/cancelled，再由取得执行租约的恢复 Worker 转为 `running/finalization + terminal_intent=cancelled`。如果可以在同一请求内完成 M6，则直接返回终态 `LoopRunResult`；否则返回 `CancelAccepted`，不得把 waiting 状态直接改成终态。
- 对已经终态的 run，取消请求幂等返回原 `LoopRunResult`；同一 `cancel_request_id` 的重复请求返回原结果，不同取消原因不改写首次审计。若工具支持取消信号则向下传递，但不能假定数据库或 Sandbox 已立即停止。
- 运行 deadline 持久化为 UTC `deadline_at`。恢复时重新计算剩余时间；已经过期直接进入 timeout Finalization，不重新调用 ContextEngine、Planner 或工具。
- 恢复 `running/execute_tool` 时先查询已提交动作和 `ToolExecutionStore`。已有成功结果只补写 RunObservation；已有进行中或副作用不确定记录转为 conflict/确认；只有明确未开始且工具幂等策略允许时才能执行。
- 恢复 `running/finalization` 时只进入 M6 的幂等收尾，不回到 Plan；如果最终 checkpoint 已存在但 ledger 未完成，必须调用 `FinalizationService.reconcile()`；如果最终 checkpoint 尚未存在，则从原 `running/finalization` checkpoint 重建确定性的 `FinalizationInput` 并继续 `finalize()`。两条路径都不能回到 ContextEngine、Planner 或 Tool Runtime。
- 终态 checkpoint 已写入且 ledger 处于 `history_saved`、`checkpoint_saved` 或 `run_released` 时，恢复只能做 M6 对账；不得把该运行重新标记为 `running`。
- SSE 客户端断开不等于用户取消。服务端运行是否继续由部署策略控制；若请求级任务会随连接取消，必须先落 checkpoint，再由状态查询或 resume 接管。

### 6.9 Protocol 与装配边界

~~~python
from collections.abc import AsyncIterator
from typing import Literal, Protocol

from app.agent.harness.contracts import HarnessGraphState, NextAction
from app.agent.harness.finalization import FinalizationInput


class HarnessRunStore(Protocol):
    async def claim_new_run(self, command: StartRunCommand) -> None: ...
    async def load_state(self, run_ref: HarnessRunRef) -> HarnessGraphState: ...
    async def acquire_execution_lease(
        self,
        run_ref: HarnessRunRef,
        *,
        owner_id: str,
        lease_seconds: int,
    ) -> RunExecutionFence: ...
    async def renew_execution_lease(
        self,
        run_ref: HarnessRunRef,
        *,
        fence: RunExecutionFence,
        lease_seconds: int,
    ) -> RunExecutionFence: ...
    async def assert_execution_fence(
        self, run_ref: HarnessRunRef, *, fence: RunExecutionFence
    ) -> None: ...
    async def release_execution_lease(
        self, run_ref: HarnessRunRef, *, fence: RunExecutionFence
    ) -> None: ...
    async def save_phase(
        self,
        run_ref: HarnessRunRef,
        state: HarnessGraphState,
        *,
        fence: RunExecutionFence,
        expected_version: int,
    ) -> None: ...
    async def mark_terminal_intent(
        self,
        run_ref: HarnessRunRef,
        *,
        intent: str,
        error: RunError | None,
        fence: RunExecutionFence,
        expected_version: int,
    ) -> None: ...


class ConfirmationStore(Protocol):
    async def prepare(
        self, run_ref: HarnessRunRef, request: ConfirmationRequest, *, digest: str
    ) -> ConfirmationRecord: ...
    async def publish(
        self, run_ref: HarnessRunRef, confirmation_id: str, *, digest: str
    ) -> ConfirmationRecord: ...
    async def resolve(
        self, run_ref: HarnessRunRef, reply: ConfirmationReply
    ) -> Literal["confirmed", "rejected", "idempotent"]: ...


class HarnessEventSink(Protocol):
    async def emit(self, event: HarnessEvent) -> None: ...


class LoopController(Protocol):
    async def start(self, command: StartRunCommand) -> LoopRunResult: ...
    async def restore(self, command: RestoreRunCommand) -> LoopRunResult: ...
    async def resume(self, command: ResumeRunCommand) -> LoopRunResult: ...
    async def cancel(
        self, command: CancelRunCommand
    ) -> CancelAccepted | LoopRunResult: ...
    async def stream_start(
        self, command: StartRunCommand
    ) -> AsyncIterator[HarnessEvent | LoopRunResult]: ...
    async def stream_restore(
        self, command: RestoreRunCommand
    ) -> AsyncIterator[HarnessEvent | LoopRunResult]: ...
    async def stream_resume(
        self, command: ResumeRunCommand
    ) -> AsyncIterator[HarnessEvent | LoopRunResult]: ...
~~~

单元测试使用内存 fake 实现 `HarnessRunStore`、`ConfirmationStore`、M2 Context builder、M3 Planner、M4 ToolRuntime、M6 FinalizationService 和 EventSink；不能在 Loop Controller 测试中连接真实 LLM、DW、Qdrant 或 Neo4j。

### 5.9.1 Run Lease 与 Fencing 规则

**需要新增：版本与 Checkpoint 写入协调。** `state_version` 是控制状态 CAS，`FinalizationLedgerState.version` 是独立账本 CAS，`fencing_token` 是 Worker 租约 epoch；`checkpoint_version` 是 Harness 业务协调层分配的单调修订号，并非 Saver 原生整数 CAS。`harness_runs` 增加 `checkpoint_revision/checkpoint_id/checkpoint_digest`，动作记录保留 `base_checkpoint_revision/candidate_checkpoint_id/payload_digest`。`read_checkpoint_revision(run_ref)` 从这一业务映射读取。所有版本独立递增，不用租约 token 替代数据版本。

Checkpoint 写入必须经同一个 M5 写入协调入口：在 run 级数据库锁内复核 lease、fence 和预期修订，向 Saver 写入带候选 digest 的 checkpoint，再发布业务修订映射；取得/接管租约也使用该锁。Saver 写入与业务提交不是同一事务，写入响应未知时禁止盲目重写，必须按候选标识读取对账。恢复和 Working Memory 只读取已发布映射指向的 checkpoint，不以 Saver 的无条件 latest 作为业务事实。锁丢失或 fence 过期立即停止写入；旧请求即使最终落入 Saver，也不能发布映射、覆盖新 owner 的有效状态或授权执行。此入口需用实际 Saver API 做 M1/M6 阶段集成测试，不假设官方提供 fenced CAS。

prepared 动作必须保留可重建的受控 payload 和 digest，不能只保存不可逆摘要。恢复先读正式动作记录：committed 则重建同一动作；prepared 且候选 checkpoint 匹配则在新 fence 下补 committed；无候选证明则沿用同一动作补 checkpoint，不重新规划；内容冲突进入受控失败。旧 fence 仅作为历史证明，新 owner 不能继续使用它写入。动作提交完成后重新加载状态，再按已提交 payload 分派。

终态 checkpoint 与账本完成不是同一时刻。账本未 completed 时，M5 允许取得同一 run 的收尾专用租约，调用 M6 reconcile，禁止运行 Planner/Tool；账本完成后释放。真正终态指 checkpoint 与主账本均完成。Formation 后台任务使用独立 claim lease，不延长 Harness 执行租约。

`active_run_id` 只表达会话当前业务运行，不授予某个进程执行权。M5 另外在 `harness_runs` 保存 `lease_owner_id/lease_expires_at/fencing_token/heartbeat_at`；`fencing_token` 每次首次取得或过期后接管租约时单调加一，同一 owner 的正常续租不增加 token。租约使用数据库 UTC 时间判断，不能依赖 Worker 本地时钟。

执行规则固定为：

1. `start/restore/resume` 在进入 Loop 前必须取得执行租约；未过期租约被其他 owner 持有时返回运行中快照，不启动第二个循环。
2. Worker 在 Context、Planner、Tool、Checkpoint、Confirmation 和 Finalization 等外部边界前后续租或验证 fence；预计耗时超过剩余租约时先续租。
3. 所有 `harness_runs`、`harness_actions`、`harness_tool_executions`、Artifact 提交和 Checkpoint 协调写入都携带当前 fencing token。数据库只接受与当前 token 相等的写入；更小 token 一律 conflict。
4. 租约到期只允许新 owner 接管，不自动把 run 标为失败。新 owner 先按 phase、checkpoint、动作和工具执行记录恢复；不得从头重新执行。
5. API cancel 只设置持久化 `cancel_requested`，不抢占执行租约。当前 owner 在安全点进入 cancelled Finalization；owner 消失后由取得更大 fencing token 的恢复 Worker 完成取消。
6. `waiting_confirmation` 和真正终态不持有执行租约；进入等待或 M6 完成后条件释放。`running/finalization` 在收尾未结束时仍需要租约和 fence。
7. 续租、接管、旧 token 拒绝、双 Worker 工具执行、租约期间 API 重入和恢复接管必须有数据库集成测试。

`build_harness_graph()` 和旧 `build_agent_graph()` 共用应用启动时已 setup 的同一个 `AsyncPostgresSaver`，但同一 `thread_id` 同一时刻只能有一个写入图。迁移期间由配置选择入口：未启用 Harness 的请求继续走旧图；启用后该会话的所有新 run 固定走 Harness，不能在暂停期间切回旧图。`MemoryClientManager` 的 Working Memory loader 在 Harness 成为默认入口时切换到 `checkpointed_harness_graph`，避免从旧图读取另一份状态。

### 6.10 核心伪代码

~~~text
async start(command: StartRunCommand) -> LoopRunResult:
    state = await initialize_new_run(command)
    return await continue_running(state)

async restore(command: RestoreRunCommand) -> LoopRunResult:
    ledger = await finalization_ledger.load(run_ref=command.run_ref)
    checkpoint = await load_checkpoint_for_route(command.run_ref)
    if checkpoint is not None and is_terminal(checkpoint.harness.status):
        verify_restore_identity(checkpoint, command.run_ref)
        if ledger is None:
            raise FinalizationError("终态 checkpoint 缺少收尾账本，不能按普通 restore 继续")
        # 这里只读路由到 M6，不把终态 checkpoint 当作普通 restore 成功。
        return await finalization_service.reconcile(command.run_ref)
    state = await restore_process(command)
    if state.harness.phase == finalization:
        if await finalization_checkpoint_exists(command.run_ref, ledger):
            # 终态 checkpoint 已存在但 ledger 未收口，只能进入 M6 对账。
            return await finalization_service.reconcile(command.run_ref)
        value = build_finalization_input_from_checkpoint(state)
        return await finalization_service.finalize(value)
    if ledger is not None and ledger.finalization_status != completed:
        reject_if(state.harness.phase != finalization)
        if await finalization_checkpoint_exists(command.run_ref, ledger):
            return await finalization_service.reconcile(command.run_ref)
        value = build_finalization_input_from_checkpoint(state)
        return await finalization_service.finalize(value)
    reject_if(state.harness.status == waiting_confirmation)
    return await continue_running(state)

async resume(command: ResumeRunCommand) -> LoopRunResult:
    state = await load_and_validate_waiting_state(command.run_ref)
    reject_if(state.harness.status != waiting_confirmation)
    resolution = await confirmation_store.resolve(
        command.run_ref, command.reply
    )
    if resolution == idempotent:
        return await load_current_result_or_conflict(command.run_ref)
    if command.reply.decision == reject:
        state = transition(
            state,
            status=running,
            phase=finalization,
            terminal_intent=cancelled,
        )
        persist_checkpoint(state)
        return await finalize(state, intent=cancelled)
    state = merge_whitelisted_conditions(state, command.reply)
    state = transition(state, status=running, phase=restore_run)
    persist_checkpoint(state)
    emit(run.resumed)
    return await continue_running(state)

async continue_running(state):
    reject_if(state.harness.status != running)
    while state.harness.status == running:
        if cancelled(state):
            return await finalize(state, intent=cancelled)
        if deadline_exceeded(state):
            return await finalize(state, intent=timeout)
        if state.harness.iteration >= policy.max_iterations:
            return await finalize(state, intent=failed, error=max_iterations)

        state = transition(state, phase=build_context)
        compiled = await retry_context_build(state, policy)

        state = transition(state, phase=plan)
        next_action = await retry_planner(
            compiled_context=compiled,
            state_view=project_planner_state(state),
            tool_specs=tool_registry.list_specs(),
            issuance=next_issuance(state),
        )
        state.harness.iteration += 1

        state = transition(state, phase=validate_action)
        commit_result = await action_committer.commit(
            ActionCommitRequest(
                run_id=state["run_id"],
                action=next_action,
                expected_action_seq=next_action.action_seq,
                expected_state_version=state.harness.state_version,
                expected_checkpoint_version=await read_checkpoint_revision(run_ref(state)),
                execution_fence=fence,
            )
        )
        assert commit_result.action_seq == next_action.action_seq
        state = await run_store.load_state(run_ref(state))
        assert state.harness.action_seq == commit_result.action_seq

        if next_action.action_type == ask_user:
            return await pause(state, next_action.ask_user)

        if next_action.action_type == final_answer:
            return await finalize(
                state,
                intent=completed,
                final_answer=next_action.final_answer,
            )

        state = transition(state, phase=execute_tool)
        while true:
            result = await execute_or_reuse_tool(next_action.tool_call, state)
            if result.status != temporary_error:
                break
            if not can_retry_same_action(result, state):
                break
            increment_tool_retry_count(state, result.tool_call_id)
            persist_checkpoint(state)
            # 同一 action_id、同一参数 digest；不增加 iteration/action_seq。

        state = transition(state, phase=handle_tool_result)

        if result.status == needs_user:
            return await pause(state, convert_to_confirmation(result))

        state = transition(state, phase=record_observation)
        append_bounded_observation(state, map_tool_result(result))
        persist_checkpoint(state)

        if result.status in {success, partial, temporary_error}:
            continue  # 下一轮先重新 BuildContext
        if planner_can_compensate(result, state):
            continue  # 新 action，不复用旧 action_id
        return await finalize(state, intent=failed, error=map_error(result))
~~~

伪代码中的 `initialize_new_run`、`load_checkpoint_for_route`、`is_terminal`、`verify_restore_identity`、`restore_process`、`load_and_validate_waiting_state`、`finalization_checkpoint_exists`、`build_finalization_input_from_checkpoint`、`continue_running`、`merge_whitelisted_conditions`、`load_current_result_or_conflict`、`convert_to_confirmation`、`retry_context_build`、`execute_or_reuse_tool`、`pause`、`planner_can_compensate` 和 `persist_checkpoint` 是 M5/M6 边界上的内部职责描述，不是当前已有函数。`start` 只创建新 run；`restore` 先只读判断是否存在终态 checkpoint：缺少 ledger 时直接报收尾错误，存在 ledger 时无论是否已完成都只调用 M6 `reconcile()`，绝不把终态当作普通恢复；其余情况才处理进程中断或服务重启，且禁止从 `waiting_confirmation` 进入普通循环；`resume` 只处理已发布确认，禁止把普通 `running` 当作用户确认恢复。没有最终 checkpoint 时，`restore` 从原 `running/finalization` checkpoint 调用现有 `build_turn_output()`，重建确定性的受控 `FinalizationInput` 并调用 M6 `finalize()`；两条路径都不得回到 ContextEngine、Planner 或 Tool Runtime。`ActionCommitRequest` 沿用 M3 契约；`ActionCommitResult` 只证明提交状态，分派时继续使用同一个已校验 `next_action`，不得假设提交结果携带不存在的 `action` 字段。同动作工具重试停留在内层循环，保持 `action_id`、参数摘要、iteration 和 action_seq 不变；退出内层循环后才记录一次最终观察并决定重规划或失败。调用 M6 前先写 `running/finalization + terminal_intent`；M6 返回成功后才写终态。

### 6.11 字段级数据流

| 上游模块 | 上游字段 | 当前模块如何消费 | 当前模块输出字段 | 下游模块 | 下游用途 |
| --- | --- | --- | --- | --- | --- |
| API/AgentService | `input_text`、`conversation_id`、`project_id`、`asset_ids` | 生成可信身份并创建新 run；项目和附件边界随 run 固定 | `StartRunCommand` | M1/ConversationRepository | 初始化状态和 turn |
| M1 状态层 | `HarnessGraphState`、`HarnessControlState` | 校验状态、阶段、身份、版本、deadline 和预算 | 阶段转换后的状态 | Checkpointer | 可恢复运行现场 |
| M2 ContextEngine | `CompiledContext` | 构造 PlannerInput，不保存正文 | `compiled_context`、`last_context_build_id` | M3/状态审计 | 当前一次规划输入 |
| M4 Registry | `list_specs()` | 获取已注册且启用工具快照 | `PlannerInput.tool_specs` | M3 | 限制 Planner 可选工具 |
| M3 Planning Agent | `NextAction` | 提交候选 action_seq 和动作 digest | committed NextAction | M4/M5/M6 | 按三类动作唯一分派 |
| M3 | `NextAction.tool_call` | 构造运行身份和阶段明确的请求 | `ToolExecutionRequest` | M4 ToolRuntime | 执行业务工具 |
| M4 | `ToolResult.summary` | 截断并复制 | `RunObservation.summary` | M1/M2 | 下一轮上下文证据摘要 |
| M4 | `ToolResult.result_ref`、`evidence_refs`、`limitations` | 复制受控引用和限制 | RunObservation 同名字段 | M2/M6 | 下一轮规划和最终追溯 |
| M4 | `ToolResult.confirmation_request` | 服务端生成 confirmation_id | `ConfirmationRequest` | API/SSE/ConfirmationStore | 暂停并等待用户 |
| M3 | `NextAction.ask_user` | 服务端生成 confirmation_id | `ConfirmationRequest` | API/SSE/ConfirmationStore | 暂停并等待用户 |
| API | `ConfirmationReply` | 校验 pending、过期、身份和字段白名单 | `resolved_conditions` | M1/M2 | 恢复同一 run 后重新 build |
| M3 | `NextAction.final_answer` | 与 run 身份、观察、限制和终态意图组装 | `FinalizationInput` | M6 | 统一收尾 |
| M6 | `FinalizationResult` | 封装为 LoopRunResult 并发终态事件 | API/SSE Response | 客户端 | 唯一最终结果 |

### 6.12 与现有代码的衔接和文件级任务

| 任务 | 类型 | 文件/函数 | 产出 | 前置 |
| --- | --- | --- | --- | --- |
| M5.1 | 需要新增 | `app/agent/harness/loop_controller.py` | LoopController、重试/续行/暂停/取消编排 | M1～M4 |
| M5.2 | 需要新增 | `app/agent/harness/graph.py::build_harness_graph()` | 使用现有 AgentState/AgentContext 和同一 Saver 的循环图 | M5.1 |
| M5.3 | 需要新增 | `app/agent/harness/runner.py` | 同步结果和事件流共享的 HarnessRunner | M5.1/M5.2 |
| M5.4 | 需要新增 | `app/agent/harness/run_store.py` | run 单活、版本条件更新、prepared 动作对账和状态查询 | 数据库迁移 |
| M5.5 | 需要新增 | `app/agent/harness/confirmation_store.py` | pending 确认创建、过期、幂等回复和白名单条件合并 | 数据库迁移 |
| M5.6 | 需要重构 | `app/repositories/conversation_repository.py::start_turn()` | 条件设置 active_run_id；相同 run 幂等、不同 run 冲突 | M5.4 |
| M5.7 | 需要重构 | `app/services/agent_service.py::_run_async()`、`qyStream()`、`_new_identity()`、`_new_turn_state()`、`_graph_config()` | 新 run/恢复分离；同步和 SSE 共享 controller；Harness 路径不自行 Finalization | M5.3 |
| M5.8 | 需要重构 | `app/api/routers/agent.py::run_agent()`、`run_agent_stream()` | 保留旧路径并映射 Harness 结果；增加确认、状态和取消接口 | M5.3/M5.5 |
| M5.9 | 需要重构 | `app/api/dependencies.py::get_agent_service()` | 注入 ContextEngine、PlanningAgent、ToolRuntime、LoopController 和 M6 fake/实现 | M2～M5 |
| M5.10 | 需要重构 | `app/clients/postgres_client.py`、`app/clients/memory_client.py` | 编译 Harness 图；切换唯一 Working Memory 状态源 | M5.2 |
| M5.11 | 需要新增 | `tests/test_harness_loop_controller.py`、`tests/test_harness_pause_resume.py` | 主循环、重试、暂停、恢复、取消、超时和崩溃点单测 | M5.1～M5.5 |
| M5.12 | 需要新增 | `tests/test_harness_loop_integration.py` | Saver、run store、ToolExecutionStore 和 API/SSE 集成 | M5.6～M5.10 |

**过渡期保留**：`app/agent/graph.py::build_agent_graph()`、`app/agent/nodes/finalize_turn.py` 和旧 `AgentService` 结果结构继续服务未切换会话，但不再新增 Harness 行为。Harness 端到端验收和 Working Memory 状态源切换完成后，再删除旧图重复调度与旧的服务层收尾分支；M5 不提前删除旧节点。

### 6.13 测试、验收与完善判定

**单元测试**：覆盖三类动作分派、iteration、三类重试计数、最大迭代、deadline、取消、状态非法、单活冲突、prepared 动作五个崩溃点、确认字段白名单、确认过期与重复回复。

**集成测试**：覆盖相同 `AsyncPostgresSaver` 的新建/崩溃恢复、工具成功后恢复不重复 handler、暂停不写 completed、确认后同一 run/turn/thread 继续、`running/finalization` 只进入 M6、终态 checkpoint 已写但 ledger 未完成时只调用 `reconcile()`、同步和 SSE 产生相同终态、旧图配置路径保持可用。

**M5 验收标准**：

1. Loop Controller 是唯一循环和阶段推进者；M2、M3、M4、M6 均不能自行跳转主循环。
2. 工具成功或 partial 后必经 RunObservation 和新 ContextEngine build，再次规划使用最新证据。
3. 同一 action 重试保持 `action_id`；重新规划产生新 action；重试次数、iteration 和 action_seq 互不混用。
4. 暂停保留 `active_run_id`、`turn_id`、`run_id` 和 checkpoint，不调用 `finish_turn()` 或 Memory Formation。
5. resume 校验五个身份字段和 confirmation，重复回复幂等，跨 run/过期/冲突回复不修改状态。
6. running、waiting、终态和 finalization 崩溃均有确定恢复路径；进程 restore、用户 confirmation resume 和 M6 reconcile 不混用；已成功工具不重复执行。
7. 同步和 SSE 共享同一 Runner、EventSink 和 Finalization，不重复保存历史或触发记忆形成。
8. 用户级工具权限明确不在当前 M5 门禁中；运行身份只用于归属和隔离。

**完善判定**：上述职责、输入/输出、状态映射、主循环、重试、暂停恢复、崩溃对账、接口、字段流、文件任务和测试均已覆盖，M5 在文档层面判定为完善。代码层面仍属于需要新增/重构，必须依次完成 M5.1～M5.12 后才算实现完成。

## 12. Finalization + Memory Formation

> 架构模块 6；本章小节沿用模块内编号，研发阶段编号见第 21 节。

### 6.1 模块职责

**一句话职责**：把 M5 已确定的终态意图转换为一次可重试、可对账、可追溯的会话收尾，并在会话结果稳定落库后提交长期记忆形成。

**现有能力**：

- `app/agent/turn_output.py::build_turn_output()` 已按 `AgentState` 返回输出类型、可重渲染载荷和助手可见文本。
- `app/repositories/conversation_repository.py::start_turn()` 已创建用户消息、轮次和 `active_run_id`。
- `app/repositories/conversation_repository.py::finish_turn()` 已在一个事务中写助手消息、`turn_outputs`、执行轨迹并清理 `active_run_id`。
- `app/services/agent_service.py::_history_output()`、`_save_turn_finish()` 和 `_submit_memory_formation()` 已形成结果整理、历史保存和记忆提交的旧路径。
- `app/agent/memory/formation_service.py::MemoryFormationService.submit()` 已统一调用 Eligibility、Extractor、Governance、MemoryWriter 和 `MemoryManager.add()`。

**需要重构**：

- 将旧 AgentService 中分散的最终保存、错误收尾和记忆提交收拢为一个 M6 FinalizationService；同步和 SSE 只能调用同一个收尾入口。
- 让 `finish_turn()` 对相同 `turn_id + finalization_digest` 幂等，对不同摘要返回冲突；助手消息、结构化输出和执行轨迹不能在恢复时重复插入。
- 将 `finalize_turn()` 从每次经过旧图就追加消息的隐式节点改为可验证的 Working Memory 消息构造和一次性 checkpoint 提交边界。
- 为 `MemoryFormationService.submit()` 增加稳定 `formation_key` 查重；当前每次调用生成新的 `formation_run_id`，不能直接满足恢复幂等。

**需要新增**：

- `app/agent/harness/finalization.py` 的 FinalizationService、Finalization DTO、状态映射、摘要生成和失败隔离。
- M5 `harness_runs` 的 finalization 协调字段，或等价的 Finalization Ledger；记录历史提交、checkpoint 提交和 formation 提交进度。
- `MemoryFormationRunModel.formation_key` 唯一约束，以及 `TurnMemoryInput.formation_key` 字段。

**不负责**：继续规划、执行工具、重建上下文、决定是否询问用户、直接调用 `MemoryManager.add()` 或重新实现 Eligibility、Extractor、Governance、MemoryWriter。`waiting_confirmation` 不进入 M6；M5 必须先保存暂停现场并返回。

### 6.2 输入 DTO 与状态资格

M6 只接受 M5 已写入 `running/finalization` 的终态意图。它不接受 `waiting_confirmation`，也不接受从业务阶段直接传来的终态。

~~~python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from app.agent.harness.contracts import (
    ContractModel,
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
    RunExecutionFence,
)
from app.agent.harness.state import HarnessControlState
from app.agent.memory.contracts import MemoryFormationResult, TurnMemoryInput
from app.agent.memory.enums import MemoryFormationStatus


class TraceEntry(ContractModel):
    phase: LoopPhase
    action_id: str | None = None
    tool_name: str | None = None
    status: str = Field(min_length=1, max_length=32)
    duration_ms: int = Field(default=0, ge=0)
    error_code: str | None = None


class ExecutionTraceSummary(ContractModel):
    entries: tuple[TraceEntry, ...] = ()
    total_iterations: int = Field(default=0, ge=0)


class FinalizationMemoryMetadata(ContractModel):
    """随最终输出进入 Formation 的受控来源与限制元数据。"""

    result_refs: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[str, ...] = Field(default=(), max_length=64)
    limitations: tuple[str, ...] = Field(default=(), max_length=32)


class FinalizationInput(ContractModel):
    run_ref: HarnessRunRef
    execution_fence: RunExecutionFence
    terminal_intent: Literal["completed", "failed", "cancelled", "timeout"]
    control_state: HarnessControlState
    input_text: str = Field(min_length=1, max_length=20_000)
    execution_mode: str = Field(default="single_query", min_length=1, max_length=64)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    output_type: str = Field(min_length=1, max_length=64)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    assistant_content: str = Field(default="", max_length=20_000)
    output_quality: Literal["complete", "partial"] = "complete"
    result_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    execution_trace: ExecutionTraceSummary = Field(default_factory=ExecutionTraceSummary)
    error_message: str = Field(default="", max_length=2_000)
    requested_at: datetime

    @model_validator(mode="after")
    def validate_terminal_input(self) -> "FinalizationInput":
        if str(self.control_state.get("status")) != HarnessStatus.RUNNING.value:
            raise ValueError("FinalizationInput 必须来自 running/finalization")
        if str(self.control_state.get("phase")) != LoopPhase.FINALIZATION.value:
            raise ValueError("FinalizationInput 的 control_state.phase 必须为 finalization")
        if str(self.control_state.get("terminal_intent")) != self.terminal_intent:
            raise ValueError("terminal_intent 必须与 control_state 一致")
        if "state_version" not in self.control_state:
            raise ValueError("FinalizationInput.control_state 必须携带 state_version")
        if "fencing_token" not in self.control_state:
            raise ValueError("FinalizationInput.control_state 必须携带 fencing_token")
        if self.control_state["fencing_token"] != self.execution_fence.fencing_token:
            raise ValueError("FinalizationInput 的 fence 必须与控制状态一致")
        if self.terminal_intent == "completed" and not self.assistant_content:
            raise ValueError("completed 收尾必须有受控助手内容")
        if self.terminal_intent != "completed" and not self.error_message and not self.assistant_content:
            raise ValueError("非 completed 收尾必须有错误说明或受控提示")
        if self.terminal_intent != "completed" and self.output_quality != "complete":
            raise ValueError("只有 completed intent 可以标记 output_quality=partial")
        if self.output_quality == "partial" and not self.limitations:
            raise ValueError("partial 输出必须说明 limitations")
        if len(set(self.result_refs)) != len(self.result_refs):
            raise ValueError("result_refs 不能重复")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence_refs 不能重复")
        return self


class FinalizationResult(ContractModel):
    run_ref: HarnessRunRef
    status: HarnessStatus
    # partial 是会话输出质量，不是新的 Harness 生命周期状态。
    output_status: Literal["completed", "partial", "failed", "cancelled", "timeout"]
    output_type: str = Field(min_length=1, max_length=64)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    assistant_content: str = ""
    result_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    finalization_digest: str = Field(min_length=64, max_length=64)
    formation_key: str | None = Field(default=None, min_length=64, max_length=64)
    history_saved: bool
    checkpoint_saved: bool
    active_run_released: bool
    memory_submission_status: Literal[
        "not_attempted", "accepted", "failed", "unknown", "not_configured"
    ] = "not_attempted"
    memory_formation: MemoryFormationResult | None = None

    @model_validator(mode="after")
    def validate_terminal_result(self) -> "FinalizationResult":
        terminal_statuses = {
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
            HarnessStatus.TIMEOUT,
        }
        if self.status not in terminal_statuses:
            raise ValueError("FinalizationResult.status 必须是 Harness 终态")
        if self.status is HarnessStatus.COMPLETED:
            if self.output_status not in {"completed", "partial"}:
                raise ValueError("completed Harness 只能映射 completed/partial 输出")
        elif self.output_status != self.status.value:
            raise ValueError("非 completed Harness 的 output_status 必须与终态一致")
        if not (self.history_saved and self.checkpoint_saved and self.active_run_released):
            raise ValueError("FinalizationResult 只能表示主收尾步骤均已完成的结果")
        if self.memory_submission_status == "not_attempted":
            raise ValueError("最终结果不能保留 not_attempted 的 Formation 提交状态")
        if self.memory_submission_status == "accepted":
            if self.memory_formation is None:
                raise ValueError("accepted 必须携带 memory_formation")
            if self.memory_formation.status is MemoryFormationStatus.FAILED:
                raise ValueError("failed Formation 不能映射为 accepted")
        elif self.memory_submission_status == "not_configured":
            if self.memory_formation is not None:
                raise ValueError("not_configured 不能携带 memory_formation")
        elif self.memory_submission_status == "unknown":
            if self.memory_formation is not None:
                raise ValueError("unknown 提交结果不能伪装成已知 Formation 结果")
            if self.formation_key is None:
                raise ValueError("unknown 提交结果必须保留 formation_key 供恢复对账")
        elif self.memory_submission_status == "failed":
            if (
                self.memory_formation is not None
                and self.memory_formation.status is not MemoryFormationStatus.FAILED
            ):
                raise ValueError("failed 必须没有 Formation 结果或携带 failed Formation 结果")
        return self


class FinalizationLedgerState(ContractModel):
    run_ref: HarnessRunRef
    finalization_digest: str = Field(min_length=64, max_length=64)
    version: int = Field(default=0, ge=0)
    finalization_status: Literal[
        "pending",
        "history_saved",
        "checkpoint_saved",
        "run_released",
        "completed",
    ] = "pending"
    terminal_status: HarnessStatus | None = None
    output_status: Literal["completed", "partial", "failed", "cancelled", "timeout"] | None = None
    output_type: str | None = Field(default=None, min_length=1, max_length=64)
    history_saved: bool = False
    checkpoint_saved: bool = False
    active_run_released: bool = False
    memory_submission_status: Literal[
        "not_attempted", "accepted", "failed", "unknown", "not_configured"
    ] = "not_attempted"
    formation_key: str | None = Field(default=None, min_length=64, max_length=64)
    formation_run_id: str | None = None
    finalization_error: str | None = None
    history_saved_at: datetime | None = None
    checkpoint_saved_at: datetime | None = None
    active_run_released_at: datetime | None = None
    memory_submitted_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def validate_progress(self) -> "FinalizationLedgerState":
        progress = {
            "pending": (False, False, False),
            "history_saved": (True, False, False),
            "checkpoint_saved": (True, True, False),
            "run_released": (True, True, True),
            "completed": (True, True, True),
        }
        expected_flags = progress[self.finalization_status]
        actual_flags = (
            self.history_saved,
            self.checkpoint_saved,
            self.active_run_released,
        )
        if actual_flags != expected_flags:
            raise ValueError("FinalizationLedgerState 的主步骤不能回退或跳过")
        if self.finalization_status == "completed" and (
            self.memory_submission_status == "not_attempted"
            or self.completed_at is None
        ):
            raise ValueError("completed 账本必须有 Formation 状态和 completed_at")
        if self.finalization_status != "completed" and self.completed_at is not None:
            raise ValueError("未完成账本不能设置 completed_at")
        terminal_statuses = {
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
            HarnessStatus.TIMEOUT,
        }
        if self.terminal_status not in terminal_statuses:
            raise ValueError("账本必须保存 Harness 终态")
        if self.output_status is None or self.output_type is None:
            raise ValueError("账本必须保存 output_status 和 output_type")
        if self.terminal_status is HarnessStatus.COMPLETED:
            if self.output_status not in {"completed", "partial"}:
                raise ValueError("completed Harness 只能映射 completed/partial 输出")
        elif self.output_status != self.terminal_status.value:
            raise ValueError("非 completed Harness 的 output_status 必须与终态一致")
        timestamp_pairs = (
            (self.history_saved, self.history_saved_at),
            (self.checkpoint_saved, self.checkpoint_saved_at),
            (self.active_run_released, self.active_run_released_at),
        )
        for completed, timestamp in timestamp_pairs:
            if completed != (timestamp is not None):
                raise ValueError("账本步骤和时间戳必须同步")
        ordered_timestamps = tuple(
            timestamp
            for timestamp in (
                self.history_saved_at,
                self.checkpoint_saved_at,
                self.active_run_released_at,
            )
            if timestamp is not None
        )
        if ordered_timestamps != tuple(sorted(ordered_timestamps)):
            raise ValueError("主收尾时间戳必须按 history/checkpoint/release 单调递增")
        if self.memory_submission_status == "accepted":
            if self.finalization_status not in {"run_released", "completed"}:
                raise ValueError("Formation 只能在 active run 释放后记录")
            if not self.formation_key or not self.formation_run_id or not self.memory_submitted_at:
                raise ValueError("accepted Formation 必须保存 key、run_id 和提交时间")
            if self.memory_submitted_at < self.active_run_released_at:
                raise ValueError("Formation 提交时间不能早于 active run 释放")
        elif self.memory_submission_status == "not_configured":
            if self.formation_key or self.formation_run_id or self.memory_submitted_at:
                raise ValueError("not_configured 不能保存 Formation 任务字段")
        elif self.memory_submission_status == "failed":
            if self.finalization_status not in {"run_released", "completed"}:
                raise ValueError("Formation 只能在 active run 释放后记录")
            if not self.finalization_error or self.memory_submitted_at is None:
                raise ValueError("failed Formation 必须保存脱敏错误摘要和尝试时间")
            if self.memory_submitted_at < self.active_run_released_at:
                raise ValueError("Formation 尝试时间不能早于 active run 释放")
        elif self.memory_submission_status == "unknown":
            if self.finalization_status not in {"run_released", "completed"}:
                raise ValueError("unknown Formation 提交结果只能在 active run 释放后记录")
            if not self.formation_key or not self.finalization_error or not self.memory_submitted_at:
                raise ValueError("unknown Formation 必须保存 key、脱敏错误摘要和尝试时间")
            if self.memory_submitted_at < self.active_run_released_at:
                raise ValueError("Formation 尝试时间不能早于 active run 释放")
        if self.completed_at is not None:
            if self.completed_at < self.active_run_released_at:
                raise ValueError("账本完成时间不能早于 active run 释放")
            if self.memory_submitted_at is not None and self.completed_at < self.memory_submitted_at:
                raise ValueError("账本完成时间不能早于 Formation 提交或失败时间")
        return self


class FinalizationHistoryWriter(Protocol):
    async def finish_turn(
        self,
        *,
        conversation_id: str,
        user_id: str,
        thread_id: str,
        turn_id: str,
        run_id: str,
        execution_mode: str,
        status: str,
        assistant_content: str,
        output_type: str,
        output_payload: dict[str, Any],
        execution_trace: ExecutionTraceSummary,
        error_message: str,
        finalization_digest: str,
        execution_fence: RunExecutionFence,
    ) -> Literal["saved", "idempotent"]: ...

    async def release_active_run(
        self,
        *,
        conversation_id: str,
        user_id: str,
        run_id: str,
        conversation_status: str,
        finalization_digest: str,
        execution_fence: RunExecutionFence,
    ) -> Literal["released", "idempotent"]: ...


class FinalizationCheckpointWriter(Protocol):
    async def save_final_checkpoint(
        self,
        *,
        run_ref: HarnessRunRef,
        terminal_status: HarnessStatus,
        control_state: HarnessControlState,
        assistant_message: "CheckpointMessage",
        expected_human_message_id: str,
        expected_state_version: int,
        expected_checkpoint_version: int,
        finalization_digest: str,
        execution_fence: RunExecutionFence,
    ) -> "FinalCheckpointRecord": ...


class FinalizationLedger(Protocol):
    async def begin(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        terminal_status: HarnessStatus,
        output_status: Literal["completed", "partial", "failed", "cancelled", "timeout"],
        output_type: str,
        execution_fence: RunExecutionFence,
    ) -> FinalizationLedgerState: ...

    async def load(
        self, *, run_ref: HarnessRunRef
    ) -> FinalizationLedgerState | None: ...
    async def mark_history_saved(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        expected_status: Literal["pending"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...
    async def mark_checkpoint_saved(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        expected_status: Literal["history_saved"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...
    async def mark_active_run_released(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        expected_status: Literal["checkpoint_saved"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...
    async def mark_memory_submitted(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        formation_key: str,
        formation_run_id: str,
        expected_status: Literal["run_released"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...
    async def mark_memory_failed(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        error: str,
        expected_status: Literal["run_released"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...
    async def mark_memory_unknown(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        formation_key: str,
        error: str,
        expected_status: Literal["run_released"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...
    async def mark_memory_not_configured(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        expected_status: Literal["run_released"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...
    async def mark_finalization_completed(
        self,
        *,
        run_ref: HarnessRunRef,
        digest: str,
        memory_submission_status: Literal["accepted", "failed", "unknown", "not_configured"],
        expected_status: Literal["run_released"],
        expected_version: int,
        execution_fence: RunExecutionFence,
    ) -> None: ...


class FinalizationResultReader(Protocol):
    async def read(
        self, *, run_ref: HarnessRunRef, digest: str
    ) -> FinalizationResult | None: ...


class PersistedFinalizationOutput(ContractModel):
    """由历史结果和受控 turn output 重建 Finalization 的最小事实。"""

    run_ref: HarnessRunRef
    finalization_digest: str = Field(min_length=64, max_length=64)
    terminal_status: HarnessStatus
    terminal_intent: Literal["completed", "failed", "cancelled", "timeout"]
    input_text: str = Field(min_length=1, max_length=20_000)
    execution_mode: str = Field(min_length=1, max_length=64)
    project_id: str | None = None
    asset_ids: tuple[str, ...] = ()
    output_status: Literal["completed", "partial", "failed", "cancelled", "timeout"]
    output_type: str = Field(min_length=1, max_length=64)
    output_payload: dict[str, Any] = Field(default_factory=dict)
    assistant_content: str = ""
    result_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    execution_trace: ExecutionTraceSummary = Field(default_factory=ExecutionTraceSummary)
    error_message: str = ""


class CheckpointMessage(ContractModel):
    """写入 AgentState.messages 的受控消息描述；运行时转换为 HumanMessage/AIMessage。"""

    message_id: str = Field(min_length=1, max_length=256)
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)
    content_digest: str = Field(min_length=64, max_length=64)
    turn_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    asset_ids: tuple[str, ...] = Field(default=(), max_length=32)
    output_type: str | None = Field(default=None, min_length=1, max_length=64)
    finalization_digest: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_role_payload(self) -> "CheckpointMessage":
        if self.role == "user":
            if self.output_type is not None or self.finalization_digest is not None:
                raise ValueError("HumanMessage 不能携带最终输出字段")
        elif not self.output_type or not self.finalization_digest:
            raise ValueError("AIMessage 必须绑定 output_type 和 finalization_digest")
        return self


class FinalCheckpointRecord(ContractModel):
    run_ref: HarnessRunRef
    finalization_digest: str = Field(min_length=64, max_length=64)
    terminal_status: HarnessStatus
    control_state: HarnessControlState
    checkpoint_id: str = Field(min_length=1)
    # Harness 协调修订号，映射 Saver checkpoint_id，不是 Saver 原生 CAS。
    checkpoint_version: int = Field(ge=0)
    # HarnessControlState 的逻辑版本；FinalizationLedgerState.version 另行计数。
    state_version: int = Field(ge=0)
    messages: tuple[CheckpointMessage, ...] = Field(min_length=2)
    human_message_id: str = Field(min_length=1)
    human_message_digest: str = Field(min_length=64, max_length=64)
    output_type: str = Field(min_length=1)
    assistant_message_id: str = Field(min_length=1)
    assistant_message_digest: str = Field(min_length=64, max_length=64)
    saved_at: datetime

    @model_validator(mode="after")
    def validate_message_set(self) -> "FinalCheckpointRecord":
        human_messages = tuple(
            message for message in self.messages if message.role == "user"
            and message.turn_id == self.run_ref.turn_id
            and message.run_id == self.run_ref.run_id
        )
        assistant_messages = tuple(
            message for message in self.messages if message.role == "assistant"
            and message.turn_id == self.run_ref.turn_id
            and message.run_id == self.run_ref.run_id
        )
        if len(human_messages) != 1 or len(assistant_messages) != 1:
            raise ValueError("当前 turn/run 必须各有一条 HumanMessage 和 AIMessage；允许历史消息")
        if len({message.message_id for message in self.messages}) != len(self.messages):
            raise ValueError("消息 ID 不得重复")
        human = human_messages[0]
        assistant = assistant_messages[0]
        if human.content_digest != self.human_message_digest:
            raise ValueError("HumanMessage 正文摘要不匹配")
        if assistant.output_type != self.output_type:
            raise ValueError("AIMessage output_type 不匹配")
        if human.message_id != self.human_message_id:
            raise ValueError("最终 checkpoint 的 HumanMessage ID 不匹配")
        if assistant.message_id != self.assistant_message_id:
            raise ValueError("最终 checkpoint 的 AIMessage ID 不匹配")
        if assistant.finalization_digest != self.finalization_digest:
            raise ValueError("AIMessage 必须绑定当前 finalization_digest")
        if assistant.content_digest != self.assistant_message_digest:
            raise ValueError("assistant_message_digest 必须与 AIMessage 正文一致")
        if human.turn_id != assistant.turn_id or human.run_id != assistant.run_id:
            raise ValueError("HumanMessage 和 AIMessage 必须属于同一 turn/run")
        return self


class FinalizationReconciliationSource(Protocol):
    async def read_persisted_output(
        self, *, run_ref: HarnessRunRef, digest: str
    ) -> PersistedFinalizationOutput | None: ...

    async def read_final_checkpoint(
        self, *, run_ref: HarnessRunRef, digest: str
    ) -> FinalCheckpointRecord | None: ...

    async def read_formation(
        self, *, formation_key: str
    ) -> MemoryFormationResult | None: ...


class MemoryFormationPort(Protocol):
    async def submit(self, turn: TurnMemoryInput) -> MemoryFormationResult: ...


class FinalizationService(Protocol):
    async def finalize(self, value: FinalizationInput) -> FinalizationResult: ...
    async def reconcile(
        self, run_ref: HarnessRunRef, *, execution_fence: RunExecutionFence
    ) -> FinalizationResult: ...
~~~

`FinalizationInput` 的 `output_type`、`output_payload` 和 `assistant_content` 必须来自 M5 调用现有 `build_turn_output(final_state)` 的结果；`project_id` 和 `asset_ids` 来自经过 M1/M2 身份与附件访问边界校验的最终状态，不允许客户端在收尾阶段改写。`output_quality` 由已持久化的工具/报告状态确定，不能由 Planner 自行声称。M6 不复制现有问数、报告或聊天分支。完整 `AgentState`、Prompt、SQL、Python 源码、完整 rows 和原始事件不进入该 DTO。`FinalizationHistoryWriter` 是重构后的 `ConversationRepository.finish_turn()` 目标契约；当前实现缺少 `thread_id`、`run_id` 和 `finalization_digest`，属于需要重构。

### 6.3 输出状态映射与持久化位置

| M5 终态意图 | M6 Harness status | 会话 status | output_status | completed_at | active_run_id | Memory Formation |
| --- | --- | --- | --- | --- | --- | --- |
| `completed` 且输出完整 | `completed` | `completed` | `completed` | 写入 | 清空 | 历史成功后提交，Eligibility 决定是否实际形成 |
| `completed` 但报告/任务部分可用 | `completed` | `partial` | `partial` | 写入 | 清空 | 以 `status="partial"` 提交，候选仍由治理决定 |
| `failed` | `failed` | `failed` | `failed` | 写入 | 清空 | 历史成功后可提交，通常由 Eligibility 跳过 |
| `cancelled` | `cancelled` | `cancelled` | `cancelled` | 写入 | 清空 | 可提交审计输入，但不生成取消原因之外的长期事实 |
| `timeout` | `timeout` | `timeout` | `timeout` | 写入 | 清空 | 可提交审计输入，不能覆盖超时答案 |
| `waiting_confirmation` | 不进入 M6 | `waiting_confirmation` | 不适用 | 保持空 | 保持当前 run | 不提交 |

`partial` 是会话输出质量和数据库轮次状态，不新增 `HarnessStatus.PARTIAL`。`FinalizationResult.status` 只使用 M1 已冻结的 HarnessStatus；`ConversationRepository.finish_turn()` 的会话 `status` 才可以使用 `partial`。

### 6.4 与现有结果整理和会话仓储的衔接

1. M5 进入 `running/finalization` 后调用 `app/agent/turn_output.py::build_turn_output(final_state)`，得到唯一的 `output_type`、`output_payload` 和 `assistant_content`。
2. `app/services/agent_service.py::_history_output()` 继续复用该函数；`_save_turn_finish()` 和 `_submit_memory_formation()` 改为委托 M6，不能被同步和 SSE 各调用一次。
3. `app/repositories/conversation_repository.py::start_turn()` 只负责新 run 的用户消息、turn 和 active run；resume 不调用它，不创建新的用户消息。
4. M6 调用重构后的 `ConversationRepository.finish_turn()`，一次保存 assistant message、结构化输出和受控执行轨迹；该事务不再提前清空 `active_run_id`，也不把完整工具正文塞入聊天历史。
5. `app/agent/nodes/finalize_turn.py::finalize_turn()` 只作为旧图兼容入口或消息构造逻辑；Harness 图不能让旧节点和 M6 同时追加同一 turn 的消息。

当前 `finish_turn()` 的真实行为是查询 turn/conversation，写 assistant message、业务 `turn_outputs` 和 `execution_trace`，然后设置 `completed_at`、清空 `active_run_id` 并提交。它没有 finalization 幂等键；恢复重试会重新生成 message/output UUID，不能直接满足幂等。因此该函数属于需要重构，而不是现有幂等能力。Harness 目标实现把“保存轮次结果”和“释放会话 active run”拆成两个可对账步骤：先幂等保存历史，再保存最终 checkpoint，最后通过 `WHERE active_run_id = :run_id` 的条件更新释放 run。

### 6.5 Working Memory、最终 Checkpoint 与持久化顺序

Working Memory 有两个落点：会话历史表保存用户可见事实，Checkpointer 的 `AgentState.messages` 保存下一次上下文读取所需的消息状态。M6 是 Harness 路径的唯一收尾写入者。

~~~text
M5: running/finalization + terminal_intent
    -> build_turn_output(final_state)
    -> canonicalize controlled output
    -> calculate finalization_digest
    -> FinalizationLedger pre-check
    -> ConversationRepository.finish_turn()
       assistant message + turn output + execution trace
       turn completed_at; active_run_id remains current run
    -> save final Harness Checkpoint
       terminal status + digest + bounded messages/references
    -> ConversationRepository.release_active_run()
       conditionally clear active_run_id and publish conversation terminal status
    -> MemoryFormationService.submit(TurnMemoryInput)
    -> FinalizationResult
~~~

历史结果必须先于最终 checkpoint 落库，因为客户端可见答案不能在历史事实不存在时被标记为完成。checkpoint 保存失败时，ledger 保留 `history_saved`，恢复从原 `running/finalization` checkpoint 再次调用现有 `build_turn_output()`，重建同一受控 `FinalizationInput` 并只补 checkpoint 和后续步骤，不重复写 assistant message；`finalization_digest` 不一致时必须返回 conflict。在最终 checkpoint 成功前不得释放 `active_run_id`。释放失败时保留 `checkpoint_saved`，恢复只执行条件释放和后续步骤。Memory Formation 未装配时记录 `memory_submission_status=not_configured`；提交被接受时记录 `accepted`，包括现有服务返回 `pending`、`skipped` 或已完成/部分完成结果，但 `accepted` 只表示提交边界成功，不表示长期记忆已经形成完成；提交调用抛出异常，或返回 `MemoryFormationStatus.FAILED` 时记录 `failed`。这些记忆层失败均不得覆盖已经成功的历史、checkpoint、active run 释放和主运行结果。

Working Memory 消息规则：

- 新 run 在初始 checkpoint 中写入一次 HumanMessage，消息 ID 固定为 `turn:{turn_id}:user`；数据库用户消息仍由 `ConversationRepository.start_turn()` 写入，二者使用同一 `turn_id` 对账。resume、restore 和 M6 都不得再次创建 HumanMessage。
- M6 在最终 checkpoint 中追加一次 AIMessage，消息 ID 固定为 `turn:{turn_id}:assistant`，metadata 必须包含 `turn_id/run_id/output_type/finalization_digest`。消息正文只使用受控 `assistant_content`，报告、完整 rows、trace 和源码只保存引用。
- `FinalizationCheckpointWriter.save_final_checkpoint()` 必须接收受控 `CheckpointMessage`、`expected_human_message_id` 和 `expected_checkpoint_version`，读取原 `AgentState.messages` 后验证 HumanMessage 存在，再通过 `add_messages` reducer 追加 AIMessage；不能只保存 `HarnessControlState`。
- 相同 `assistant_message_id + finalization_digest` 重试返回原 `FinalCheckpointRecord`；相同消息 ID 但正文摘要、输出类型或 digest 不同返回 conflict。Checkpoint 已包含相同 AIMessage 时不得重复追加。
- 会话历史表是用户可见历史事实，Checkpointer 是 Working Memory 状态源。二者必须以 `turn_id/run_id/finalization_digest` 对账；任何一方都不能通过读取另一方并重新生成随机消息 ID 来“修复”冲突。
- 现有 `finalize_turn()` 按 `len(messages)` 计算序号，无法独自保证恢复幂等；Harness 路径必须改为上述确定性 turn 级消息键。旧图在迁移期保持原行为，但不得与 Harness 同时写同一个 turn。

### 6.6 Finalization 幂等和崩溃对账

`finalization_digest` 使用规范化后的 `terminal_intent`、`run_ref`、输出类型、受控 output payload、助手文本、结果引用、证据引用、限制和错误摘要计算 SHA-256。不得把随机 UUID、对象地址或未脱敏的完整异常文本纳入摘要。

目标 ledger 可以作为 M5 `harness_runs` 的扩展字段，不强制新增第二张重复表。至少需要：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `finalization_digest` | `VARCHAR(64)` | 同一收尾输入的幂等身份 |
| `finalization_status` | `pending/history_saved/checkpoint_saved/run_released/completed` | 主收尾进度；不被记忆增强失败覆盖 |
| `memory_submission_status` | `not_attempted/accepted/failed/unknown/not_configured` | Formation 提交边界，与任务处理状态和主收尾状态分离 |
| `history_saved_at` | UTC timestamp | 会话事务已提交 |
| `checkpoint_saved_at` | UTC timestamp | 最终 checkpoint 已提交 |
| `active_run_released_at` | UTC timestamp | 已按当前 run 条件清理会话 active run |
| `formation_key` | `VARCHAR(64)` nullable | Formation 的稳定幂等键 |
| `formation_run_id` | `VARCHAR(128)` nullable | 已提交的形成任务 |
| `memory_submitted_at` | UTC timestamp nullable | Formation 提交边界完成时间 |
| `completed_at` | UTC timestamp nullable | Finalization ledger 收口时间 |
| `finalization_error` | 脱敏文本 nullable | 只记录收尾错误分类或短消息 |

必须处理以下崩溃点：

| 崩溃点 | 恢复动作 | 禁止行为 |
| --- | --- | --- |
| digest 生成前 | 重新从 M5 状态构造同一输入 | 生成随机 finalization key |
| `finish_turn()` 提交前 | 重新执行历史事务 | 发送 completed 事件 |
| `finish_turn()` 已提交、ledger 未更新 | 以 turn/output 唯一键和 digest 对账，标记 history_saved | 再插入 assistant 消息 |
| history 已保存、checkpoint 未保存 | 只补最终 checkpoint | 回到 Planner 或 Tool Runtime |
| checkpoint 已保存、active run 未释放 | 只按 `active_run_id=run_id` 条件释放 | 清除另一个 run 或重写 checkpoint |
| active run 已释放、formation 未提交 | 只补提交 formation | 重写会话结果 |
| formation 已创建、响应丢失 | 按 `formation_key` 读取原任务结果 | 创建新的 formation_run_id |
| Formation 抛出异常 | 标记 `memory_submission_status=failed`，保留主结果 | 覆盖会话状态或重复写答案 |

相同 `run_id + finalization_digest` 重复调用返回原 `FinalizationResult`；相同 `run_id` 但摘要不同返回 `conflict`，不得覆盖原答案。

`FinalizationService.reconcile(run_ref)` 是终态 checkpoint 已写入但账本未收口时的唯一恢复入口。它先读取 `FinalizationLedgerState`、已持久化的受控 turn output、最终 checkpoint 和 Formation 审计；四者的 `run_ref` 与 `finalization_digest` 必须一致。对账只修复账本状态标记、补齐缺失的 `active_run` 条件释放、Formation 提交、Formation 状态记录和 `FinalizationResult` 重建；不写会话历史、不重复写终态 Checkpoint，也不得重新调用 ContextEngine、Planning Agent 或 Tool Runtime。

对账规则固定如下：

1. 没有 ledger、最终 checkpoint 或受控 turn output 时，返回受控 `FinalizationError`，不得猜测终态，也不得创建新的 finalization digest。
2. `finalization_status=pending` 或 `history_saved` 时，只根据已存在的受控 turn output 和最终 checkpoint 补齐 ledger 标记；不得补写会话历史或重新保存 checkpoint。
3. `finalization_status=checkpoint_saved` 时，只按 `active_run_id=run_id` 条件释放；这些阶段都不回到 M5 主循环。
4. `finalization_status=run_released` 且 `memory_submission_status=not_attempted` 时，只按 `formation_key` 读取或提交 Formation，并最终标记 `completed`；若已经是 `failed`，只读取失败审计并完成主账本，不由正常对账自动重试。
5. Formation 已有 `formation_key` 记录但响应丢失时，复用审计结果；正常请求不得因为 `memory_submission_status=failed` 自动重复提交，维护性补偿任务必须使用同一 `formation_key`。
6. 每次账本更新都必须携带 `finalization_digest`、`expected_status` 和 `expected_version`；条件更新成功后版本递增，冲突时重新读取，禁止跳过阶段或回退。

~~~text
async reconcile(run_ref: HarnessRunRef) -> FinalizationResult:
    ledger = await finalization_ledger.load(run_ref=run_ref)
    if ledger is None:
        raise FinalizationError("收尾账本不存在，不能对账")
    if ledger.finalization_status == completed:
        previous = await finalization_result_reader.read(
            run_ref=run_ref, digest=ledger.finalization_digest
        )
        if previous is None:
            raise FinalizationError("已完成账本但无法重建最终结果")
        return previous
    output = await reconciliation_source.read_persisted_output(
        run_ref=run_ref, digest=ledger.finalization_digest
    )
    checkpoint = await reconciliation_source.read_final_checkpoint(
        run_ref=run_ref, digest=ledger.finalization_digest
    )
    if output is None or checkpoint is None:
        raise FinalizationError("终态对账缺少受控输出或最终 checkpoint")
    verify_same_identity_and_digest(ledger, output, checkpoint)

    if not ledger.history_saved:
        # 只修复 ledger 事实标记，不重新写会话历史。
        await finalization_ledger.mark_history_saved(
            run_ref=run_ref,
            digest=ledger.finalization_digest,
            expected_status="pending",
            expected_version=ledger.version,
        )
        ledger = await finalization_ledger.load(run_ref=run_ref)

    if not ledger.checkpoint_saved:
        # checkpoint 已存在时只补账本，不重复写 checkpoint；不存在才由受控输出补写。
        await finalization_ledger.mark_checkpoint_saved(
            run_ref=run_ref,
            digest=ledger.finalization_digest,
            expected_status="history_saved",
            expected_version=ledger.version,
        )
        ledger = await finalization_ledger.load(run_ref=run_ref)

    if not ledger.active_run_released:
        await history_writer.release_active_run(
            conversation_id=run_ref.conversation_id,
            user_id=run_ref.user_id,
            run_id=run_ref.run_id,
            conversation_status=map_history_status(output),
            finalization_digest=ledger.finalization_digest,
        )
        await finalization_ledger.mark_active_run_released(
            run_ref=run_ref,
            digest=ledger.finalization_digest,
            expected_status="checkpoint_saved",
            expected_version=ledger.version,
        )
        ledger = await finalization_ledger.load(run_ref=run_ref)

    memory_result = await load_or_submit_formation(ledger, output)
    ledger = await finalization_ledger.load(run_ref=run_ref)
    if ledger is None:
        raise FinalizationError("收尾账本丢失")
    await finalization_ledger.mark_finalization_completed(
        run_ref=run_ref,
        digest=ledger.finalization_digest,
        memory_submission_status=ledger.memory_submission_status,
        expected_status="run_released",
        expected_version=ledger.version,
    )
    return rebuild_finalization_result(output, ledger, memory_result)
~~~

上面 `verify_same_identity_and_digest`、`load_or_submit_formation` 和 `rebuild_finalization_result` 都是 M6 待实现的内部函数。`read_final_checkpoint()` 返回已存在的终态 checkpoint；对账不应重复保存同一 checkpoint。若实现发现 checkpoint 记录存在但账本仍为 `history_saved`，只做账本状态推进；如果最终 checkpoint 缺失，对账必须失败并等待 M6 `finalize()` 从原始收尾输入补写，不能由 `reconcile()` 猜测或重新生成输入。每个 `mark_*` 成功后必须重新读取 ledger，使用递增后的 `version` 进行下一步更新。

### 6.7 Memory Formation 接入和幂等

M6 的长期记忆调用链固定为：

~~~text
Finalization
    -> MemoryFormationService.submit(TurnMemoryInput)
    -> Eligibility
    -> Extractor
    -> Governance
    -> MemoryWriter
    -> MemoryManager.add()
~~~

Finalization 不能直接调用 `MemoryManager.add()`。M6 只构造受控 `TurnMemoryInput` 并消费现有 `MemoryFormationResult`；Eligibility、Extractor、Governance 和 MemoryWriter 仍由 `app/agent/memory/formation_service.py` 负责。

现有 `TurnMemoryInput` 位于 `app/agent/memory/contracts.py`，在原类上增加稳定形成键，不创建同名第二套 DTO：

~~~python
class TurnMemoryInput(BaseModel):
    # 保留现有身份、输入、输出、execution_mode、status、project_id 和 asset_ids 字段。
    formation_key: str = Field(min_length=64, max_length=64)
~~~

`formation_key` 由 `finalization_digest` 派生，并绑定 `turn_id`、`run_id` 和 Memory Formation 版本。`MemoryFormationService.submit()` 的目标顺序是先按 `formation_key` 查询 `MemoryFormationRunModel`，命中则返回可恢复结果；未命中时以唯一约束插入；并发冲突重新读取已有任务。当前实现每次 `submit()` 都用 `uuid4()` 新建 `formation_run_id`，`memory_formation_runs` 也没有 `formation_key`，两者都属于需要重构。现有自动 Formation 会返回 `MemoryFormationStatus.PENDING` 并在后台处理，因此 M6 的 `accepted` 仅表示幂等提交已创建或命中，不表示长期记忆已经形成完成；最终状态继续从 Formation 审计表查询。

`FinalizationInput -> TurnMemoryInput` 的字段映射固定为：五个运行身份中 `user_id/conversation_id/turn_id/run_id` 原样传递，`thread_id` 不进入现有 Memory DTO；`input_text`、`assistant_content`、`execution_mode`、`output_type`、`output_payload` 原样受控传递；`output_status -> status`；`project_id -> project_id`；`asset_ids -> asset_ids`。`result_refs/evidence_refs/limitations` 由 Finalization 保存进受控 `output_payload` 的固定元数据区域后随之进入 Formation，不给现有 `TurnMemoryInput` 再造重复字段。附件 ID 必须是本轮已验证集合，确保 Perceptual Memory 的来源治理仍能校验所属关系。

`MemoryFormationRunModel` 的目标迁移是增加非空 `formation_key`、唯一约束和按 key 查询索引，保留 `formation_run_id` 作为任务 ID。`MemoryFormationResult` 继续复用 `app/agent/memory/contracts.py` 的现有模型，不在 M6 复制候选决定字段。

### 6.8 失败、部分结果和取消语义

- 完整结果：`output_status="completed"`，助手消息和结构化输出可见，结果引用和证据引用可追溯。
- 部分结果：`output_status="partial"`，仍可保存和返回；`limitations` 必须说明失败任务、截断、缺失映射或失败组件。不能新增 `HarnessStatus.PARTIAL`。
- 不可恢复失败：`output_status="failed"`，只保存受控错误提示和执行轨迹；不能把异常对象或完整 traceback 写入历史。
- 用户取消：`output_status="cancelled"`，使用服务端固定提示或已有安全摘要；不把未完成规划文本当作答案。
- 超时：`output_status="timeout"`，明确运行超时；不把已超时的工具结果伪装成完整结论。
- `waiting_confirmation`：只由 M5 返回 `ConfirmationRequest`，不调用 `finish_turn()`、不写 `completed_at`、不清 `active_run_id`、不写最终 assistant message、不提交 Formation。

### 6.9 与 AgentService、旧图和 API/SSE 的衔接

| 代码位置 | 当前状态 | M6 处理 |
| --- | --- | --- |
| `app/services/agent_service.py::_history_output()` | **现有能力**：调用 `build_turn_output()` | **需要重构**：只负责构造受控 FinalizationInput 字段 |
| `app/services/agent_service.py::_save_turn_finish()` | **现有能力**：直接调用 `finish_turn()` | **需要重构**：委托 M6，不能由同步和 SSE 各调用一次 |
| `app/services/agent_service.py::_submit_memory_formation()` | **现有能力**：历史成功后 submit | **需要重构**：移入 M6，使用 `formation_key` 并隔离 Formation 失败 |
| `app/repositories/conversation_repository.py::start_turn()` | **现有能力**：新 run 写用户消息和 active run | **直接复用**：resume 和 waiting 不调用 |
| `app/repositories/conversation_repository.py::finish_turn()` | **现有能力但非幂等** | **需要重构**：增加身份、digest、条件更新和 insert-on-conflict 对账 |
| `app/agent/turn_output.py::build_turn_output()` | **现有能力**：统一输出整理 | **直接复用**，不复制问数/报告/聊天分支 |
| `app/agent/nodes/finalize_turn.py::finalize_turn()` | **现有能力**：旧图追加 Working Memory 消息 | **需要重构**：Harness 只允许 M6 作为唯一收尾写入者 |
| `app/agent/memory/formation_service.py::submit()` | **现有能力但新任务非幂等** | **需要重构**：按 `formation_key` 读取或创建 |
| `app/models/memory.py::MemoryFormationRunModel` | **现有能力**：Formation 审计表 | **需要重构**：增加 key、唯一约束和查询索引 |

旧 `build_agent_graph()` 和旧 AgentService 路径在迁移期继续工作，但一个 `thread_id` 只能选择一个收尾所有者。Harness 开启后，旧 `finalize_turn` 节点不再挂在该会话图上；SSE 只订阅 M5 EventSink，不能自行调用历史保存或 Formation。

### 6.10 核心伪代码

~~~text
async finalize(value: FinalizationInput) -> FinalizationResult:
    memory_formation: MemoryFormationPort | None = injected_memory_formation
    reject_if(value.control_state.status != running)
    reject_if(value.control_state.phase != finalization)
    reject_if(value.control_state.terminal_intent != value.terminal_intent)
    reject_if(value.terminal_intent not in {completed, failed, cancelled, timeout})
    history_status = map_history_status(value)
    output_status = map_output_status(value)
    controlled_output_payload = embed_finalization_metadata(
        value.output_payload,
        FinalizationMemoryMetadata(
            result_refs=value.result_refs,
            evidence_refs=value.evidence_refs,
            limitations=value.limitations,
        ),
    )
    digest = sha256(canonical_controlled_payload(
        value, output_payload=controlled_output_payload
    ))
    terminal_status = map_harness_status(value)

    ledger = await finalization_ledger.load(run_ref=value.run_ref)
    if ledger is not None and ledger.finalization_digest != digest:
        raise ConflictError("同一 run 的收尾输入不一致")
    if ledger is not None and ledger.finalization_status == completed:
        previous = await finalization_result_reader.read(
            run_ref=value.run_ref, digest=digest
        )
        if previous is None:
            raise FinalizationError("收尾账本已完成但无法重建最终结果")
        return previous
    if ledger is None:
        ledger = await finalization_ledger.begin(
            run_ref=value.run_ref,
            digest=digest,
            terminal_status=terminal_status,
            output_status=output_status,
            output_type=value.output_type,
        )

    if not ledger.history_saved:
        save_status = await history_writer.finish_turn(
            conversation_id=value.run_ref.conversation_id,
            user_id=value.run_ref.user_id,
            thread_id=value.run_ref.thread_id,
            turn_id=value.run_ref.turn_id,
            run_id=value.run_ref.run_id,
            execution_mode=value.execution_mode,
            status=history_status,
            assistant_content=value.assistant_content,
            output_type=value.output_type,
            output_payload=controlled_output_payload,
            execution_trace=value.execution_trace,
            error_message=value.error_message,
            finalization_digest=digest,
        )
        if save_status not in {saved, idempotent}:
            raise FinalizationError("会话结果保存失败")
        await finalization_ledger.mark_history_saved(
            run_ref=value.run_ref,
            digest=digest,
            expected_status="pending",
            expected_version=ledger.version,
        )

    ledger = await finalization_ledger.load(run_ref=value.run_ref)
    if ledger is None:
        raise FinalizationError("收尾账本丢失")
    if not ledger.checkpoint_saved:
        terminal_control_state = transition_finalization_to_terminal(
            value.control_state, terminal_status=terminal_status
        )
        await checkpoint_writer.save_final_checkpoint(
            run_ref=value.run_ref,
            terminal_status=terminal_status,
            control_state=terminal_control_state,
            assistant_message=CheckpointMessage(
                message_id=f"turn:{value.run_ref.turn_id}:assistant",
                role="assistant",
                content=value.assistant_content,
                content_digest=sha256(value.assistant_content.encode("utf-8")).hexdigest(),
                turn_id=value.run_ref.turn_id,
                run_id=value.run_ref.run_id,
                output_type=value.output_type,
                finalization_digest=digest,
            ),
            expected_human_message_id=f"turn:{value.run_ref.turn_id}:user",
            expected_state_version=value.control_state["state_version"],
            expected_checkpoint_version=await read_checkpoint_revision(value.run_ref),
            finalization_digest=digest,
        )
        await finalization_ledger.mark_checkpoint_saved(
            run_ref=value.run_ref,
            digest=digest,
            expected_status="history_saved",
            expected_version=ledger.version,
        )

    ledger = await finalization_ledger.load(run_ref=value.run_ref)
    if ledger is None:
        raise FinalizationError("收尾账本丢失")
    if not ledger.active_run_released:
        release_status = await history_writer.release_active_run(
            conversation_id=value.run_ref.conversation_id,
            user_id=value.run_ref.user_id,
            run_id=value.run_ref.run_id,
            conversation_status=history_status,
            finalization_digest=digest,
        )
        if release_status not in {released, idempotent}:
            raise FinalizationError("会话 active run 释放失败")
        await finalization_ledger.mark_active_run_released(
            run_ref=value.run_ref,
            digest=digest,
            expected_status="checkpoint_saved",
            expected_version=ledger.version,
        )

    memory_result = None
    memory_submission_status = "not_attempted"
    ledger = await finalization_ledger.load(run_ref=value.run_ref)
    if ledger is not None and ledger.memory_submission_status != "not_attempted":
        memory_submission_status = ledger.memory_submission_status
        if ledger.memory_submission_status == "accepted":
            if not ledger.formation_key:
                raise FinalizationError("accepted Formation 缺少 formation_key")
            memory_result = await reconciliation_source.read_formation(
                formation_key=ledger.formation_key
            )
            if memory_result is None:
                raise FinalizationError("accepted Formation 无法从审计记录重建")
    elif memory_formation is None:
        memory_submission_status = "not_configured"
        await finalization_ledger.mark_memory_not_configured(
            run_ref=value.run_ref,
            digest=digest,
            expected_status="run_released",
            expected_version=ledger.version,
        )
    else:
        try:
            formation_key = derive_formation_key(value.run_ref, digest)
            memory_result = await memory_formation.submit(
                TurnMemoryInput(
                    formation_key=formation_key,
                    user_id=value.run_ref.user_id,
                    conversation_id=value.run_ref.conversation_id,
                    turn_id=value.run_ref.turn_id,
                    run_id=value.run_ref.run_id,
                    input_text=value.input_text,
                    assistant_content=value.assistant_content,
                    execution_mode=value.execution_mode,
                    status=output_status,
                    output_type=value.output_type,
                    output_payload=controlled_output_payload,
                    project_id=value.project_id,
                    asset_ids=list(value.asset_ids),
                )
            )
            if memory_result.status == MemoryFormationStatus.FAILED:
                memory_submission_status = "failed"
                await finalization_ledger.mark_memory_failed(
                    run_ref=value.run_ref,
                    digest=digest,
                    error=redact(memory_result.error_message),
                    expected_status="run_released",
                    expected_version=ledger.version,
                )
            else:
                memory_submission_status = "accepted"
                await finalization_ledger.mark_memory_submitted(
                    run_ref=value.run_ref,
                    digest=digest,
                    formation_key=formation_key,
                    formation_run_id=memory_result.formation_run_id,
                    expected_status="run_released",
                    expected_version=ledger.version,
                )
        except Exception as exc:
        except FormationSubmissionUnknown as exc:
            memory_submission_status = "unknown"
            await finalization_ledger.mark_memory_unknown(
                run_ref=value.run_ref,
                digest=digest,
                formation_key=formation_key,
                error=redact(exc),
                expected_status="run_released",
                expected_version=ledger.version,
                execution_fence=value.execution_fence,
            )
        except FormationBusinessFailure as exc:
        # LedgerConflict 和账本写入异常必须向上抛出，由 M6 对账处理，不得归入 Formation 失败。
            memory_submission_status = "failed"
            await finalization_ledger.mark_memory_failed(
                run_ref=value.run_ref, digest=digest, error=redact(exc),
                expected_status="run_released", expected_version=ledger.version,
                execution_fence=value.execution_fence,
            )

    ledger = await finalization_ledger.load(run_ref=value.run_ref)
    if ledger is None:
        raise FinalizationError("收尾账本丢失")
    await finalization_ledger.mark_finalization_completed(
        run_ref=value.run_ref,
        digest=digest,
        memory_submission_status=memory_submission_status,
        expected_status="run_released",
        expected_version=ledger.version,
    )

    return FinalizationResult(
        run_ref=value.run_ref,
        status=terminal_status,
        output_status=output_status,
        output_type=value.output_type,
        output_payload=controlled_output_payload,
        assistant_content=value.assistant_content,
        result_refs=value.result_refs,
        evidence_refs=value.evidence_refs,
        limitations=value.limitations,
        finalization_digest=digest,
        history_saved=True,
        checkpoint_saved=True,
        active_run_released=True,
        memory_submission_status=memory_submission_status,
        memory_formation=memory_result,
    )
~~~

`map_history_status`、`map_output_status`、`map_harness_status`、`embed_finalization_metadata`、`canonical_controlled_payload`、`transition_finalization_to_terminal`、`derive_formation_key` 和 `redact` 是 M6 内部待实现函数，不是当前代码中已存在的接口。`finalization_result_reader` 通过 `FinalizationResultReader` 从已保存的受控输出、ledger 和 Formation 审计状态重建原结果；不能依赖进程内缓存。伪代码只负责收尾编排；Eligibility、Extractor、Governance、MemoryWriter 的内部逻辑仍由现有 Memory Formation 服务负责。正常 API 重放不会在 `memory_submission_status=failed` 时反复提交 Formation；维护性补偿任务可使用同一个 `formation_key` 重试，但不能改写主运行结果。

### 6.11 字段级数据流

| 上游模块 | 上游字段 | 当前模块如何消费 | 当前模块输出字段 | 下游模块 | 下游用途 |
| --- | --- | --- | --- | --- | --- |
| M5 Loop Controller | `terminal_intent`、`run_ref` | 校验只来自 `running/finalization` 的终态输入 | `FinalizationInput.terminal_intent/run_ref` | M6 | 决定生命周期和身份边界 |
| M5/AgentState | `input_text`、`execution_mode` | 与最终状态一起封装 | 同名字段 | ConversationRepository/Memory Formation | 保存轮次和形成输入 |
| M5/AgentState | `project_id`、`asset_ids` | 复用已校验的项目和附件边界 | `FinalizationInput.project_id/asset_ids` | ConversationRepository/Memory Formation | 项目范围和感知记忆来源校验 |
| `build_turn_output()` | `output_type` | 不复制业务分支，直接校验 | `FinalizationInput.output_type` | ConversationRepository/API | 选择前端渲染类型 |
| `build_turn_output()` | `output_payload` | JSON 安全校验和大小限制 | `FinalizationInput.output_payload` | turn_outputs/Memory Formation | 受控结构化结果 |
| `build_turn_output()` | `assistant_content` | 脱敏、长度和终态资格校验 | `FinalizationInput.assistant_content` | conversation_messages/API | 用户可见回答 |
| M1/M4/M5 | `result_ref`、`evidence_refs`、`limitations` | 去重并保留引用，不展开大对象 | `FinalizationInput.result_refs/evidence_refs/limitations` | turn_outputs/Memory Formation | 追溯和限制说明 |
| M5 | `execution_trace` 摘要 | 映射为 `ExecutionTraceSummary` | 同名受控字段 | turn_outputs/Tracing | 审计，不保存 raw event |
| M6 | 受控输入全量 | 规范化计算摘要 | `finalization_digest` | FinalizationLedger/Checkpoint | 幂等和崩溃对账 |
| M6 | `output_status` | 映射会话状态 | `conversation_turns.status` | ConversationRepository | completed/partial/failed/cancelled/timeout |
| M6 | `assistant_content/output_payload/execution_trace` | 一次历史事务提交 | assistant message/turn output/trace output | PostgreSQL | 用户可见历史和重渲染；此时不提前释放 active run |
| M6 | `terminal_status/finalization_digest` | 最终状态 checkpoint | `harness.status/terminal_intent` | AsyncPostgresSaver | 运行恢复和状态查询 |
| M6 | `run_id/finalization_digest` | 最终 checkpoint 后执行条件释放 | `active_run_id=NULL`、会话终态 | ConversationRepository | 允许后续新 run，且不能清除别的 run |
| M6 | `formation_key`、受控轮次输出 | 历史成功后提交 | `TurnMemoryInput` | MemoryFormationService | Eligibility 到 Governance 的唯一入口 |
| Memory Formation | `MemoryFormationResult` | 只记录结果，不覆盖主答案 | `FinalizationResult.memory_formation` | API/SSE/审计 | 展示 pending/completed/failed |
| Finalization Ledger | `memory_submission_status`、`formation_run_id` | 与主收尾状态分离记录 | `FinalizationResult.memory_submission_status` | API/SSE/维护任务 | 区分未配置、已接受和提交失败 |

闭环收口为：

~~~text
M3 final_answer
    -> M5 terminal_intent
    -> M6 FinalizationInput
    -> build_turn_output / ConversationRepository
    -> final Checkpoint
    -> conditional active_run release
    -> MemoryFormationService.submit(TurnMemoryInput)
    -> FinalizationResult
    -> M5 LoopRunResult
    -> API/SSE
~~~

### 6.12 文件级任务、测试、验收与完善判定

| 任务 | 类型 | 文件/函数 | 产出 | 前置 |
| --- | --- | --- | --- | --- |
| M6.1 | 需要新增 | `app/agent/harness/finalization.py` | Finalization DTO、状态映射、digest、finalize/reconcile、历史/checkpoint/Formation 编排 | M1/M5 |
| M6.2 | 需要重构 | `app/repositories/conversation_repository.py::finish_turn()`、`release_active_run()` | 身份校验、digest、assistant/output/trace 幂等；最终 checkpoint 后条件清理 active run | M5 RunStore |
| M6.3 | 需要重构 | `app/agent/turn_output.py::build_turn_output()` | 保持现有分支语义，补充受控 payload 和引用边界 | M6.1 |
| M6.4 | 需要重构 | `app/agent/nodes/finalize_turn.py::finalize_turn()` | 旧图兼容；消息 metadata 支持 turn/digest 查重 | M6.2 |
| M6.5 | 需要重构 | `app/services/agent_service.py::_history_output()`、`_save_turn_finish()`、`_submit_memory_formation()` | 同步/SSE 统一委托 M6，去除重复保存和重复 Formation | M6.1/M6.2 |
| M6.6 | 需要重构 | `app/agent/memory/contracts.py::TurnMemoryInput` | 增加 `formation_key`，保留已有字段和验证器 | M6.1 |
| M6.7 | 需要重构 | `app/agent/memory/formation_service.py::submit()` | 按 formation key 查询、插入冲突对账和原结果返回 | M6.6 |
| M6.8 | 需要重构 | `app/models/memory.py::MemoryFormationRunModel` | key 非空唯一约束和索引 | 数据库迁移 |
| M6.9 | 需要重构 | M5 `harness_runs`/RunStore | finalization 状态、digest、checkpoint 和 formation 进度 | M5.4 |
| M6.10 | 需要新增 | `tests/test_harness_finalization.py` | 状态映射、digest、顺序和失败隔离 | M6.1-M6.9 |
| M6.11 | 需要新增 | `tests/test_harness_finalization_recovery.py` | 各崩溃点、重复提交、摘要冲突、消息/output 幂等 | M6.2/M6.7 |
| M6.12 | 需要重构 | `app/api/routers/agent.py`、SSE 事件消费 | 只从 M5 结果收口；确认、状态、取消和最终结果映射 | M5/M6 |

**单元测试**：覆盖 `FinalizationInput` 终态资格、`FinalizationResult` 的 Formation 状态约束、`FinalizationLedgerState` 单调状态/版本/时间戳校验、`partial` 与 `HarnessStatus` 分离、输出 payload 长度和引用去重、digest 稳定性、状态映射、Formation 失败隔离、`formation_key` 生成和重复/冲突判断。

**集成测试**：覆盖真实 `ConversationRepository.finish_turn()` 的 assistant/output/trace 一次提交、相同 digest 重试、不同 digest 冲突、最终 checkpoint 保存、checkpoint 已写但 active run 未释放、ledger 状态版本冲突、`FinalizationService.reconcile()` 不回到 M5、`MemoryFormationService.submit()` 的 Eligibility 到 Writer 链路、Formation 审计状态和 `MemoryManager.add()` 只通过 Writer 进入。

**端到端测试**：覆盖完整问数、部分分析结果、报告组件失败、Planner 失败、取消、超时、暂停后确认继续、同步响应、SSE 响应和旧图兼容；确认等待场景必须证明不调用 M6。

**M6 验收标准**：

1. 只有 `running/finalization` 可以调用 M6；`waiting_confirmation` 不写完成历史、不清 active run、不提交 Formation。
2. `build_turn_output()` 是最终输出整理的唯一现有逻辑来源；M6 不复制问数、报告和聊天分支。
3. 历史事务、最终 checkpoint、active run 条件释放、Formation 的顺序固定；任一步失败都有可恢复状态，终态 checkpoint 已存在时由 `reconcile()` 对账。
4. 相同 `run_id + finalization_digest` 和相同 `formation_key` 重试幂等；摘要冲突返回 conflict，不覆盖已有结果。
5. `assistant_content`、结构化输出和执行轨迹不会因同步/SSE/崩溃恢复重复写入；SSE 只传输事件。
6. `partial` 可以进入会话和最终结果，但不新增 `HarnessStatus.PARTIAL`；`failed`、`cancelled`、`timeout` 有明确可见提示和审计。
7. Finalization 只调用 `MemoryFormationService.submit()`，长期记忆仍按 Eligibility、Extractor、Governance、MemoryWriter、`MemoryManager.add()` 链路形成。
8. `finalize()` 与 `reconcile()`、相关数据库迁移、单元测试、集成测试和恢复测试全部通过后，才允许进入整体 API/SSE 和旧图迁移收口。

**完善判定**：模块 6的职责、输入/输出 DTO、状态映射、`build_turn_output()` 和会话仓储衔接、Working Memory、Checkpoint 顺序、崩溃对账、Finalization/Formation 幂等、Protocol、伪代码、字段流、文件任务、测试和验收均已覆盖。M6 在文档层面判定为完善；代码层面仍是需要新增/重构，未因文档完成而宣称已经实现。

## 13. 存储与 Checkpointer 设计
### 存储事实边界

| 数据 | LangGraph Checkpointer | PostgreSQL 业务表 | Qdrant | Neo4j | ResultArtifactStore |
| --- | --- | --- | --- | --- | --- |
| Harness 可恢复状态 | 保存 `AgentState + HarnessControlState` | `harness_runs` 保存协调和审计字段 | 否 | 否 | 否 |
| 会话、轮次和最终消息 | `messages` 作为 Working 状态 | 现有 `conversations`、`conversation_turns`、`conversation_messages`、`turn_outputs` | 否 | 否 | 否 |
| Working Memory | `AgentState.messages` 是状态源 | 不复制正文 | 否 | 否 | 否 |
| 长期记忆事实 | 否 | 现有 `agent_memories`、`memory_sources`、`memory_assets` | 仅向量投影 | Semantic 实体关系投影 | 否 |
| Context 摘要和 Trace | 否 | 现有 `context_conversation_summaries`、`context_build_runs` | 否 | 否 | 否 |
| 正式动作和工具审计 | 只保存当前引用、序号和摘要 | `harness_actions`、`harness_tool_executions` | 否 | 否 | 否 |
| ToolResult 摘要和引用 | `RunObservation` 有界保存 | 工具执行记录保存 | 否 | 否 | 不复制 |
| 完整工具结果、证据和报告 | 不保存正文 | Artifact 元数据和保留状态 | 否 | 否 | 唯一正文落点 |
| 确认请求和回复 | 保存当前 pending 摘要 | `harness_confirmations` 是确认事实源 | 否 | 否 | 否 |
| Formation 审计 | 否 | 现有 `memory_formation_runs`，增加 `formation_key` | 只保存记忆向量投影 | 只保存 Semantic 关系投影 | 否 |

PostgreSQL 是业务事实和审计来源；Qdrant 不是事实库，只保存可重建的向量投影；Neo4j 只保存 Semantic Memory 的实体关系投影；Checkpointer 只负责可恢复图状态。M4 首期 `ResultArtifactStore` 使用 PostgreSQL JSONB 作为明确实现边界，避免引入当前仓库没有的对象存储依赖；单对象受 `RuntimePolicy.max_result_bytes` 限制。以后迁移到对象存储时保持 `ref` 协议不变，但必须先实现双读、校验和删除策略。

### 新增和重构的 PostgreSQL 结构

当前项目只使用 `Base.metadata.create_all()`，没有业务迁移工具。新增表可以由它在空库创建，但它不会为已有表增加列、约束或索引。因此进入 M5/M6 集成前必须引入可回滚的版本化迁移；不能依赖 `create_all()` 完成线上升级。

| 表/变更 | 必要字段 | 约束与索引 | 为什么不能只复用现有表 |
| --- | --- | --- | --- |
| `conversations` 重构 | 新增 `agent_engine: legacy|harness` | `thread_id` 继续唯一；按 `user_id, updated_at` 保留现有索引 | 必须固定每个会话唯一执行图和 Working Memory 状态源 |
| `conversation_turns` 重构 | 新增 `finalization_digest` | `run_id` 唯一；`turn_id, finalization_digest` 对账 | 现有 turn 只保存最终业务状态，不能协调内部循环 |
| `harness_runs` 新增 | 五个身份字段、`project_id`、`asset_ids`、`status`、`phase`、`schema_version`、`version`、`iteration`、`action_seq`、三类重试摘要、`started_at`、`deadline_at`、`cancel_requested`、`terminal_intent`、`lease_owner_id`、`lease_expires_at`、`heartbeat_at`、单调 `fencing_token`、M6 ledger 字段和时间戳 | PK `run_id`；唯一 `turn_id`；`asset_ids` 使用受限 JSONB/数组并沿用最多 32 个 ID；索引 `(user_id, conversation_id, status)`、`(status, lease_expires_at)`；所有协调更新同时带 `version + fencing_token` 条件 | Checkpointer 不能代替会话单活、Worker 执行权、API 状态查询、取消标记和跨资源对账；`active_run_id` 也不能代替租约 |
| `harness_actions` 新增 | `run_id`、`action_seq`、`action_id`、`action_type`、受控 payload、`payload_digest`、`commit_status`、时间戳 | PK `(run_id, action_seq)`；唯一 `action_id`；索引 `(run_id, commit_status)` | 工具记录不能表达 ask_user/final_answer，checkpoint 也不能提供 prepared/committed 协调 |
| `harness_tool_executions` 新增 | `run_id`、`tool_call_id`、`tool_name`、`input_digest`、`result_digest`、`attempt`、`record_status`、`prepared_artifact_keys`、`fencing_token`、结果字段、错误字段、`finished_at`、`indeterminate_at`、时间戳 | PK `(run_id, tool_call_id, attempt)`；索引 `(run_id, record_status)`；相同 call 的 attempt 连续；完成提交校验当前 fencing token 和 input/result digest | 现有 `turn_outputs` 只保存最终展示结果，不能防止工具重放或确认 prepared 结果是否已提交 |
| `harness_result_artifacts` 新增 | `ref`、`run_id`、`user_id`、`tool_call_id`、`attempt`、`artifact_key`、`kind`、`ordinal`、`payload`、`payload_hash`、`content_type`、`schema_version`、`status`、`fencing_token`、`retention_until`、`orphaned_at`、`committed_at`、`deleted_at`、时间戳 | PK `ref`；唯一 `(run_id, tool_call_id, attempt, kind, ordinal)`；唯一 `artifact_key`；索引 `(run_id, tool_call_id, attempt)`、`(user_id, created_at)`、`(status, retention_until)` | Checkpointer、turn output、Context trace 都不能保存完整 rows、代码和中间产物；prepared/committed 状态必须与工具执行记录在业务库内对账 |
| `harness_confirmations` 新增 | `confirmation_id`、五个身份字段、`question`、`reason_code`、`choices`、`required_fields`、`request_digest`、`status`、`visibility`、回复摘要、`expires_at`、时间戳 | PK `confirmation_id`；部分唯一索引 `(run_id) WHERE status='pending'`，确保每个 run 只有一个未决确认；索引 `(run_id, status, visibility)`、`expires_at` | 会话消息不能作为恢复凭证，也不能表达幂等回复、发布可见性和过期 |
| `memory_formation_runs` 重构 | 新增 `formation_key` | 唯一且非空；按 key 查询索引 | 当前 `formation_run_id` 每次随机生成，无法在响应丢失后对账 |

`harness_runs` 与 Checkpointer 保存不同事实：前者用于单活、协调、查询和取消，后者保存完整可恢复状态。两者不做跨 Saver 单事务；所有跨资源步骤使用 prepared/checkpoint/committed 或 ledger 状态对账。`harness_result_artifacts` 读取必须同时校验 `ref + run_id + user_id`，删除后不得通过历史引用恢复正文。

### Checkpointer 与旧图迁移

`app/clients/postgres_client.py` 继续只创建一个 `AsyncPostgresSaver` 并只调用一次 `setup()`；Harness 不创建官方表。`build_agent_graph()` 和 `build_harness_graph()` 可以共用同一 Saver，但必须为各自配置固定的 checkpoint namespace。当前固定依赖版本是 LangGraph `1.1.6` 和 `langgraph-checkpoint-postgres` `3.1.2`；M1 实现前必须用集成测试验证 namespace 配置键和 `aget_state()` 读取方式，不能仅根据约定推断。

迁移规则：

1. 现有会话默认 `agent_engine=legacy`，迁移期继续走 `checkpointed_agent_graph`。
2. 新建会话按功能开关选择一次引擎并写入 `conversations.agent_engine`；已有非空历史会话不得在 active run 或 pending confirmation 期间切换。
3. 同一 `thread_id` 同一时刻只能有一个图写入；Harness 和旧图不得各自保存同一 turn。
4. `MemoryClientManager` 不能继续固定绑定旧图；Working loader 必须按会话 `agent_engine` 选择对应图和 namespace。Harness 成为默认入口前，这个切换必须有测试。
5. Harness 端到端、恢复和历史兼容通过后，才批量把新会话默认值切为 harness；旧图先保留，不在本轮设计中直接删除。

## 14. 指标 Meta RAG 与企业知识库边界
### Meta RAG 与企业知识库

Data Catalog / Meta RAG 只覆盖指标定义、表、字段、维度、维度值及数据结构关系；企业知识库覆盖制度、业务规则、指标口径文档、分析规范和企业文档。当前仓库只有前者，`knowledge_base` 保持禁用，不能用 Qdrant Meta collection 或 ES 维度值检索冒充企业知识库。

未来企业知识库启用后，两类来源必须在 ContextEngine 中保留独立 `source_kind/source_ref`。同一概念出现冲突时不能静默择一：Planner 生成 `ask_user`，M5 保存确认并暂停，用户选择只写入当前 run 的 `resolved_conditions`，恢复后重新 build；未经 Governance 不修改全局指标定义或长期记忆。

## 15. 暂停与恢复
### 暂停与恢复统一链路

```text
NextAction.ask_user / ToolResult.needs_user
    -> ConfirmationRequest
    -> ConfirmationStore.prepare
    -> HarnessControlState.waiting_confirmation
    -> Checkpoint
    -> ConfirmationStore.publish
    -> API/SSE waiting_confirmation
    -> ConfirmationReply
    -> 运行身份和确认凭证校验
    -> ResumeRunCommand
    -> HarnessControlState.resolved_conditions
    -> 同一 run/turn/thread 恢复
    -> ContextEngine.build()
    -> PlanningAgent.plan()
```

进程中断/服务重启恢复：

```text
RestoreRunCommand
    -> 校验 checkpoint + harness_runs + turn + active_run
    -> running 业务阶段：继续 M5 安全点
    -> running/finalization 且无终态 checkpoint：继续 M6 finalize
    -> 已有终态 checkpoint 但 ledger 未完成：M6 reconcile
```

该链路覆盖多口径、时间范围缺失、历史结果或附件引用不唯一、Meta 与未来知识库冲突、工具 needs_user、确认拒绝、确认过期和重复回复。`waiting_confirmation` 不进入 M6；确认拒绝映射为 cancelled intent；取消请求不新增 `cancelling` 状态。进程 restore 不消费 `ConfirmationReply`，用户 confirmation resume 不恢复普通 `running`；两者都保留原 `run_id/turn_id/thread_id`。

## 16. API 与 SSE
### 暂停、API 与 SSE

**现有能力**：`POST /api/agent/run` 和 `POST /api/agent/run/stream` 只支持新运行，响应是现有 `AgentRunResponse`，没有状态、确认或取消接口；`user_id` 来自 `settings.app.default_user_id`。

**需要重构/新增**：保留两个旧入口和旧响应，按 `agent_engine` 路由。Harness 路径增加：

| 接口 | 请求 | 响应 | 语义 |
| --- | --- | --- | --- |
| `POST /api/agent/run` | 扩展现有请求，可选 `project_id` | 旧会话返回 `AgentRunResponse`；Harness 会话返回版本化 Harness 响应 | 创建新 run；不得隐式恢复 |
| `POST /api/agent/run/stream` | 与同步入口相同 | SSE | 与同步入口共享同一 HarnessRunner |
| `GET /api/agent/runs/{run_id}` | path run_id，服务端当前用户 | `RunningRunSnapshot | LoopRunResult` | running 时返回只读快照；暂停或终态返回 LoopRunResult；不触发执行 |
| `POST /api/agent/runs/{run_id}/confirm` | `ResumeRunCommand`（服务端补入可信 `run_ref`） | `LoopRunResult` | 只接受 waiting_confirmation，校验运行身份和确认凭证后恢复同一 run |
| `POST /api/agent/runs/{run_id}/confirm/stream` | `ResumeRunCommand`（服务端补入可信 `run_ref`） | SSE | 与同步确认恢复共用 controller；不调用 `restore` |
| `POST /api/agent/runs/{run_id}/cancel` | `CancelRunCommand.reason`；必填 Idempotency-Key 映射 cancel_request_id | `CancelAccepted | LoopRunResult` | running/waiting 先持久化取消标记；已经终态返回原结果；不得跳过 M6 写终态 |

Harness 响应必须含 `run_ref/status/phase/iteration`；运行中查询只返回 `RunningRunSnapshot`，取消受理只返回 `CancelAccepted`，暂停和终态使用 `LoopRunResult`。暂停只携带 `pending_confirmation`；终态只携带 `finalization_result`。进程或服务重启恢复使用内部 `RestoreRunCommand`，不是用户确认 API；终态 checkpoint 对账由 M6 `reconcile()` 完成。当前未启用真实登录和用户权限校验，不能声称这些接口已完成认证授权；服务端当前用户仅用于运行归属、恢复凭证校验和结果隔离。

暂停提交顺序固定为 `ConfirmationStore.prepare -> waiting checkpoint -> ConfirmationStore.publish -> run.paused`。只有 published 请求对 API 可见；prepared 记录在恢复时与 checkpoint 对账。确认回复校验五个身份字段、`active_run_id`、`confirmation_id`、状态、摘要和过期时间；确认后只合并白名单条件，拒绝后进入 cancelled Finalization。重复同 payload 幂等，不同 payload 冲突。

SSE 使用 `HarnessEvent` 的 `event_id/event_type/run_ref/phase/status/iteration/action_id/payload/emitted_at`，事件至少覆盖 `run.started`、`context.built`、`planner.completed`、`tool.started`、`tool.completed`、`run.paused`、`run.resumed`、`run.completed`、`run.failed`、`run.cancelled` 和 `run.timeout`。流末尾发送与同步接口完全相同的 `LoopRunResult`；SSE 层不保存历史、不提交 Formation、不重复推进状态。客户端断开不等于取消，重连先查运行状态；如果需要事件补发，后续新增 Outbox，不把当前内存 EventSink 误写成持久事件总线。

## 17. 异常、重试和幂等性
### 错误、重试与终止矩阵

| 场景 | 分类 | 是否重试 | 身份/计数 | 最终处理 |
| --- | --- | --- | --- | --- |
| Planner JSON/Pydantic 错误 | planner/validation | 最多建议 2 次 | 同一上下文，不增加 iteration/action_seq | ask_user 或 failed Finalization |
| Context 临时依赖错误 | context/database | 最多建议 2 次 | 同一请求，增加 context retry | 耗尽后失败或安全重规划 |
| Context 身份、附件或请求错误 | validation/context/permission | 否 | 不推进动作 | failed Finalization |
| 工具临时错误 | tool/database/timeout | 仅 `retryable=True` 且幂等策略允许 | 同一 action_id、连续 attempt，最多建议 2 次 | 记录观察后重规划或失败 |
| 工具参数或只读校验错误 | validation/tool | 否 | 不调用 handler | 重规划或失败 |
| 工具 needs_user | user_input | 否 | 不增加重试 | 保存确认并暂停 |
| 工具 partial | 无错误或受控 limitations | 否 | 写一次观察 | 重新 build，由 Planner 决定是否继续或完成 |
| 工具执行状态不确定 | conflict | 否 | 保持执行记录 | 人工确认或受控失败，不盲目重放 |
| 用户取消 | cancelled | 否 | `cancel_requested=True` | 安全点进入 cancelled Finalization |
| 运行 deadline | timeout | 否 | 保留原 deadline | 取消下游并进入 timeout Finalization |
| Formation 失败 | Memory Formation 独立状态 | 正常请求不自动重复 | 同一 formation_key | 主运行结果保持不变，维护任务补偿 |

建议默认值是 `planner/context/tool` 各最多重试 2 次、最多 8 次 iteration、运行 5 分钟；实现时进入 `app/core/config.py`，不是当前已验证配置。重试计数写入 Checkpointer，并在 `harness_runs` 保存查询所需摘要。新的工具动作使用新 `action_id`，其 attempt 从 0 开始；重新规划不能规避同一动作的重试上限。

### 幂等键与可见性

| 副作用 | 幂等键 | 只有何时对下游可见 | 冲突处理 |
| --- | --- | --- | --- |
| 新 run/turn | `run_id`、唯一 `turn_id` | turn、run 和初始 checkpoint 对账成功 | 同会话其他 active run 返回 conflict |
| 正式动作 | `(run_id, action_seq)` + payload digest | prepared 与 checkpoint 对账并 committed | 同键不同 digest 拒绝 |
| 工具 attempt | `(run_id, action_id, attempt)` + input digest | ResultArtifact 和 ToolResult 执行记录均保存 | 跳号、摘要冲突或状态不确定拒绝 |
| Confirmation | `confirmation_id` + request/reply digest | waiting checkpoint 后 publish | 过期或不同回复返回 conflict |
| 最终输出 | `run_id + finalization_digest`；`turn_outputs` 继续受 `(turn_id, output_type)` 唯一约束 | 历史、最终 checkpoint、active run 释放均完成 | 同 run 不同 digest 拒绝 |
| Memory Formation | `formation_key` | 唯一 Formation 审计行创建或命中 | 并发冲突重新读取原任务 |

## 18. 可观测性
### Traces、Metrics 与 Logs

| 类型 | 必须覆盖 | 约束 |
| --- | --- | --- |
| Traces | `harness.run`、`context.build`、`planner.plan`、`action.validate`、`action.commit`、`tool.execute`、`observation.record`、`run.pause`、`run.resume`、`finalization`、`memory.formation.submit` | span 属性使用 ID、状态、阶段、工具名、耗时和受控错误；不写 Prompt、rows、附件正文或 reasoning |
| Metrics | `harness.run.duration`、`harness.iteration.count`、`harness.run.status`、`context.build.duration`、`context.token_count`、`context.selected_count`、`planner.duration`、`planner.token_usage`、`planner.retry_count`、`tool.call.count`、`tool.call.duration`、`tool.call.status`、`tool.retry_count`、`confirmation.request.count`、`confirmation.wait.duration`、`memory.formation.status` | 标签只用低基数 `status/phase/tool_name/error_category/agent_engine`；禁止 user/run/turn/conversation/action ID 和原文 |
| Logs | `run_id/turn_id/thread_id/conversation_id/node/phase/iteration/action_id/tool_call_id/tool_name/status/duration_ms/error_category/error_code/retry_count/context_build_id` | ID 可用于排障但不作为 metrics 标签；文本先脱敏并截断，SQL、rows、记忆正文、附件、凭证和完整异常不落日志 |

工具结果、用户问题和附件文本只允许在明确采样策略下记录哈希、长度、引用或短摘要。未知异常保存稳定错误码和服务端 trace ID；API/SSE 不返回完整 traceback、连接串或内部 Prompt。

## 19. 建议代码目录

以下是目标文件，不代表已实现。模块内文件级任务表是详细拆分依据；不创建仅改名转发的 Adapter。

| 路径 | 状态、职责与依赖 | 调用方与范围 |
| --- | --- | --- |
| `app/agent/harness/contracts.py`、`state.py` | 需要重构；纯 DTO、状态机和 codec，保留现有 AgentState | 六模块；M1 必须，长期保留 |
| `app/agent/context_engine/contracts.py`、`engine.py` | 需要重构；RuntimeContext 与编译，依赖现有 MemoryContextReader | M5；M2 必须，不另建 Engine |
| `app/agent/services/query_service.py`、`analysis_service.py`、`report_service.py` | 需要新增；提取现有节点业务逻辑，注入 DW/Meta/Sandbox | 旧节点和 M4；研发 M3 必须，按真实复用合并 |
| `app/agent/harness/tools/contracts.py`、`registry.py`、`runtime.py` | 需要新增；工具注册、调度和归一化，依赖共享 Service 和 Store | M5；研发 M4 必须，长期保留 |
| `app/agent/harness/tools/data_catalog.py`、`query_data.py`、`analyze_data.py`、`build_report.py` | 需要新增；高层工具契约，不复制业务实现 | Registry；首批启用，knowledge_base 暂不创建 |
| `app/agent/harness/planning.py`、`action_validator.py` | 需要新增；结构化规划与校验，依赖注入 LLM 和 ToolSpec | M5；研发 M5 必须 |
| `app/agent/harness/loop_controller.py`、`finalization.py` | 需要新增；唯一调度中心与幂等收尾，依赖正式 Protocol | Harness 图与 AgentService；研发 M6/M9 必须 |
| `app/agent/memory/formation_service.py`、`app/models/memory.py`、`app/repositories/memory/` | 需要重构；持久形成任务、claim 和恢复，保留 Governance/Writer | M6 与后台 worker；研发 M9 必须 |
| `app/services/agent_service.py`、`app/api/dependencies.py`、`app/api/routers/agent.py`、`app/clients/postgres_client.py`、`app/clients/memory_client.py` | 需要重构；注入、接口和旧图迁移，不搬入业务规划 | 应用入口；研发 M10 必须，旧兼容入口通过回归后再清理 |

## 20. 依赖装配
### 依赖装配

目标启动顺序：

```text
LLM / Embedding / Qdrant / Neo4j / Elasticsearch / MySQL clients
    -> PostgreSQL Engine + SessionFactory
    -> 业务 Schema 版本校验/迁移
    -> AsyncPostgresSaver.setup()
    -> legacy graph + harness graph
    -> engine-aware WorkingStateLoader
    -> MemoryRuntime
    -> ContextEngine
    -> DataCatalog/Query/Analysis/Report Services
    -> ToolRegistry + ToolRuntime
    -> PlanningAgent
    -> FinalizationService
    -> LoopController + HarnessRunner
    -> AgentService
    -> FastAPI / SSE
```

LLM、Embedding、Qdrant、Neo4j、ES、连接池、Saver、已编译图、MemoryRuntime、ContextEngine、Registry 和无请求状态的 Planner/Runtime 编排器为应用级对象。SQLAlchemy Session、Meta/DW repository、运行身份、取消信号和本次 AgentContext 为请求或工具 attempt 级对象。当前 `get_agent_service()` 注入了请求级 DW/Meta 对象，Harness 实现时要避免把这些对象捕获进应用级 Tool handler；handler 从明确的请求级依赖容器取用。任何模块都不能自行新建外部客户端。

## 21. 增量开发顺序
### M1～M11 依赖和准入

研发阶段严格按规则文档执行 M1～M11；六个模块是职责边界，不替代研发阶段编号。M7～M11 分别完成结果驱动上下文重建、暂停恢复、Finalization 接入、API/SSE 迁移，以及全链路测试与生产准入。


| 阶段 | 实现目标 | 必须复用/修改 | 明确不做 | 进入下一阶段条件 |
| --- | --- | --- | --- | --- |
| M1 状态层 | 冻结 DTO、状态机、身份、版本和 checkpoint codec | `app/agent/harness/contracts.py`、`state.py`、现有 `AgentState` | 不调用 Context/Planner/Tool | DTO、转换、JSON 往返、namespace 和旧图回归通过 |
| M2 ContextEngine | 扩展 `ContextRequest.runtime_context` 并接入状态投影 | 现有 `ContextEngine.build()`、`CompiledContext`、ContextStore | 不创建第二套 Engine/Context | runtime_context=None 回归、预算、身份和附件测试通过 |
| M3 Planning Agent | 草稿解析、动作校验、ID/候选序号发行 | `load_prompt()`、M1 DTO、Fake ToolSpec | 不执行工具或提交副作用 | 三动作、错误、能力开关、序号不变量通过 |
| M4 Tool Runtime | Registry、五类工具、共享 Service、外置结果、attempt 幂等 | Query 图、Analysis、Report、DW/Meta 能力 | 不规划、不循环；knowledge_base 禁用 | M4.1～M4.14、SQL 安全、Artifact 和真实能力集成通过 |
| M5 Loop Controller | 动作提交、循环、重试、暂停恢复、取消、超时、SSE Runner | M1～M4、同一 Saver、Conversation start | 不实现工具业务或 Formation 内部逻辑 | 崩溃恢复、成功工具不重放、同步/SSE 等价通过 |
| M6 Finalization | 幂等历史、最终 checkpoint、active run 释放、Formation 提交 | `build_turn_output()`、ConversationRepository、MemoryFormationService | 不绕过 Governance，不处理 waiting | ledger 崩溃点、formation_key、端到端和旧图迁移通过 |
| M7 工具结果驱动上下文重建 | 固化 `ToolResult -> RunObservation -> RuntimeContext -> CompiledContext` 闭环 | M2/M4/M5 | 不创建第二套 ContextEngine | 多轮证据和有界状态通过 |
| M8 暂停、确认和恢复 | 完成等待态、确认态、拒绝和取消语义 | M1/M5 | 不把 waiting_confirmation 当 completed | 重复回复、过期和重启恢复通过 |
| M9 Finalization 与 Memory Formation | 完成历史、终态 checkpoint、账本和 formation_key 对账 | M5/M6 | 不绕过 Governance | 崩溃点、未知提交和幂等恢复通过 |
| M10 API、SSE、依赖装配和旧图迁移 | 统一同步/SSE 入口并完成配置迁移 | M5/M6/AgentService | 不让 SSE 重复推进状态 | 接口、事件和旧图回归通过 |
| M11 测试、可观测性和旧代码清理 | 完成全链路准入和兼容代码收敛 | M1-M10 | 不在证据不足时设 Harness 为默认 | 全部测试和生产检查通过 |

不允许跳过模块准入：M4 完成前不能把 M5 指向真实工具；M5 完成前不能启用 M6 生产收尾；M6 完成前不能把 Harness 设为默认入口。文档设计可以协同修正前序模块，但实现提交必须保持职责边界和测试门槛。

## 22. 测试与验收标准
### 测试与验收矩阵

**单元测试**：DTO/枚举、默认容器隔离、状态转换、RuntimeContext、CompiledContext 编译、Planner 合法/非法输出、ToolSpec/参数/运行身份、ToolResult 归一化、attempt、错误分类、重试计数、幂等摘要和 Finalization 状态映射。

**集成测试**：ContextEngine 读取真实 MemoryManager；Working Memory 按图和 namespace 读取 Checkpointer；Query/Analysis/Report 调用共享 Service；DW 只读、超时和上限；PostgreSQL Run/Action/Execution/Artifact/Confirmation Store；Finalization 调用 ConversationRepository 和 MemoryFormationService；恢复不重放成功工具。

**端到端测试**：普通问数、复杂销售下降分析、工具结果驱动多轮循环、Meta 多口径确认、确认继续、确认拒绝、临时错误重试、Planner 连续失败、工具不可恢复错误、部分结果、运行超时、用户取消、同步、SSE、断线后状态查询、Finalization 崩溃恢复和旧固定图兼容。

每个阶段必须同时运行新增测试和受影响的旧图回归。生产入口切换前还要验证数据库迁移升级/回滚、Artifact 保留和删除、低基数指标、日志脱敏以及两个图不写同一 `thread_id`。

### 跨模块串联结论

完整闭环已经按唯一所有者串联：

```text
HarnessRequest
    -> M1 HarnessGraphState + HarnessControlState
    -> M2 RuntimeContext -> ContextRequest -> CompiledContext
    -> M3 PlannerInput -> NextAction
    -> M5 ActionCommitter
       -> tool_call -> M4 ToolExecutionRequest -> ToolRuntime -> ToolResult
          -> M5 RunObservation -> M2 RuntimeContext -> new CompiledContext
       -> ask_user -> ConfirmationRequest -> waiting_confirmation
          -> ConfirmationReply -> resolved_conditions -> M2 rebuild
       -> final_answer -> M6 FinalizationInput
          -> conversation history -> final Checkpoint -> active run release
          -> MemoryFormationService.submit(TurnMemoryInput)
          -> FinalizationResult -> LoopRunResult -> API/SSE
```

进程恢复分支：

```text
RestoreRunCommand
    -> M1 身份/版本校验
    -> M5 running 安全点继续
    -> M6 running/finalization finalize 或终态 checkpoint + ledger reconcile
```

字段检查结果：`ToolResult.tool_call_id == ToolCall.action_id`；`ToolResult -> RunObservation -> RuntimeContext -> CompiledContext` 已闭环；`CompiledContext -> PlannerInput` 只使用现有 DTO；`ToolRegistry.list_specs() -> PlannerInput.tool_specs` 已固定；三类动作均先提交再分派；`project_id/asset_ids` 从运行状态进入 Context、Tool 和 Formation；`memory_submission_status` 与 Harness 主终态分离；`partial` 不是 Harness 状态；`RestoreRunCommand` 与 `ResumeRunCommand` 分别对应进程恢复和用户确认恢复；终态 checkpoint 对账只通过 M6 `FinalizationService.reconcile()` 完成。

职责检查结果：M2 不选择工具；M3 不执行动作；M4 不规划或循环；M5 是唯一调度者；M6 不重新实现 Memory Governance；旧图和 Harness 图不能写同一 `thread_id`。六个模块的职责、输入输出、Protocol、伪代码、字段流、文件任务和测试均已有文档层面完善判定。

### 是否可以进入代码开发

**结论：可以进入分阶段代码开发，从 M1 开始；不可以一次性启用完整 Harness。**

当前 `app/agent/harness/contracts.py` 和 `state.py` 只是早期最小实现，与本文目标 DTO 仍不一致；M2～M6 多数类型、Store、Service、数据库结构和 API 尚未实现。每个模块必须按第 21 节的准入条件单独实现、测试和评审，不能因为文档已完善而声称功能完成。

进入 M1 时优先验证：嵌套 Pydantic/TypedDict 在 LangGraph `1.1.6` Checkpointer 中的 JSON 往返；同一 Saver 的图 namespace；五个身份字段恢复；`thread_id == conversation_id`；`project_id=None` 兼容；旧图 reducer 和节点回归。验证失败时只回到对应契约修正，不跨过 M1 直接开发 Loop Controller。

## 23. 待验证事项与明确不在本次范围内的内容
### 待验证事项

- LangGraph `1.1.6` 与 Saver `3.1.2` 的 namespace 配置、嵌套状态序列化和 `aget_state()` 行为。
- 分析同层并行时请求级 SQLAlchemy Session 是否可并发使用；必要时让 Service 为并行任务创建独立 Session。
- DW 方言下只读 SQL 解析、数据库 `statement timeout`、取消和结果上限的可靠实现。
- PostgreSQL JSONB Artifact 在真实结果规模下的容量、查询、保留和删除性能；超过首期上限后再评估对象存储。
- `create_all()` 到版本化迁移的落地工具和部署流程；在迁移方案通过前不能启用 M5/M6 新表。
- 当前默认用户模式下的运行归属边界；未来真实认证接入后另行设计工具授权，不复用 `ToolSpec.permission` 直接放行。

### 本期明确不做

本期不实现微信登录/手机号授权、支付/退款/提现、用户级工具 RBAC/ACL、企业知识库、通用 Python 执行器和持久事件 Outbox；不删除旧固定图；不把 `query_data()`、`analyze_data()`、`build_report()` 写成当前已有函数；不创建第二套 `CompiledContext`；不创建只复制字段的 Adapter；不重新创建 LangGraph 官方 Checkpointer 表；不把建议默认值写成现有配置或把设计状态写成已完成能力。
