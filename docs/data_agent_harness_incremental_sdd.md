# 数据代理 Harness 模块化增量技术设计文档

> 基线：2026-09-08 当前工作树。本文为增量设计，不表示 Harness 已实现。

## 1. 文档目标与范围

目标是在保留现有 Data Agent 能力的前提下，引入以 Loop Controller 为唯一调度中心的 Harness，支持内部循环、暂停恢复、重试、取消、最终收尾和可观测性。本次只生成设计，不修改业务代码。新增类、函数和路径均为设计建议；判断使用“现有能力”“需要重构”“需要新增”“暂不启用”“待验证”。

## 2. 已验证的现有代码基线

### 2.1 状态、图和身份

**现有能力**：`app/agent/state.py::AgentState` 是 `TypedDict(total=False)`，包含身份字段 `input_text`、`user_id`、`conversation_id`、`thread_id`、`turn_id`、`run_id`、`asset_ids`、`messages`，以及路由、分析、问数、报告、SQL 和文本输出字段。当前工作树还包含 `harness: HarnessControlState`；只有 `messages: Annotated[list[AnyMessage], add_messages]` 使用 reducer，旧节点通过 `state.get()` 读取并返回字典增量。

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

## 5. 核心 DTO 和枚举

**现有能力**：`app/agent/harness/contracts.py` 已有 `StrEnum`、Pydantic 基础 DTO 和 `AgentState` 所需的 Harness 枚举；`AgentState` 继续保持 TypedDict。**需要重构**：补齐统一字段约束、checkpoint schema version、恢复校验和状态转换。Checkpoint 只保存可恢复的控制状态和必要业务字段，写入前使用 `model_dump(mode="json")`，恢复时使用 `model_validate`。禁止以无语义裸 `dict` 作为跨模块接口。

### 5.1 状态落点与字段所有权
`HarnessGraphState` 是 LangGraph 节点边界的组合状态；`HarnessControlState` 只承载 Harness 控制信息；现有 `AgentState` 的业务字段继续由旧节点读写。`CompiledContext`、`NextAction`、`ToolResult` 和 `FinalizationResult` 是单次调用或事件 DTO，不回写成同名状态字段。

| 字段 | 所有者 | 写入时机 | 约束 |
| --- | --- | --- | --- |
| `status`, `phase`, `iteration` | Loop Controller | 每次阶段转换 | 只能通过状态转换函数修改 |
| `original_goal` | Run 初始化 | 创建新 run | resume 不覆盖 |
| `plan_progress` | Planning/Loop Controller | 规划完成或动作完成 | 仅保存摘要、依赖和完成标记，不保存隐藏思考 |
| `observations` | Tool Runtime/Loop Controller | 工具完成后 | 保存引用、摘要、状态和哈希；大结果放 Artifact |
| `pending_confirmation` | Loop Controller | 进入暂停时 | 只允许一个 pending confirmation |
| `last_error`、重试计数 | Loop Controller | 受控失败时 | 按 `action_id` 或阶段计数，不能无限重试 |
| `messages` 及现有分析/问数/报告字段 | 现有 AgentState/旧节点 | 业务节点执行时 | 不因 Harness 引入平行字段 |

### 5.2 枚举和不变量
只有 `running` 状态可以执行规划或工具动作；`waiting_confirmation` 必须保持暂停，所有终态都不可继续执行动作。第一阶段只冻结 `tool_call` 与 `final_answer` 两种动作：二者必须严格互斥，前者必须包含完整 `ToolCall`，后者必须包含非空文本。`ask_user` 及其确认字段在暂停恢复模块实施时再加入并单独验收。所有 ID 在 DTO 中使用非空字符串，`iteration >= 0`，重试计数不得小于 0。

### 5.3 最小 DTO 契约
以下是核心运行闭环的最小协议示例。M1冻结运行请求、状态快照、计划进度、观察、错误和确认边界；具体工具参数 schema 由模块 4 的 Tool Registry 绑定，确认触发和 Finalization DTO 的调用流程留给对应模块实现。**需要重构**：当前 `app/agent/harness/contracts.py::PlannerInput` 仍使用 `context: Any`，目标契约将其收紧为现有 `CompiledContext`，字段名统一为 `compiled_context`；当前实现不应被误认为已经完成。
```python
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent.context_engine.contracts import CompiledContext
from app.agent.harness.contracts import ActionType, ErrorCategory, ResultStatus


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunError(ContractModel):
    category: ErrorCategory
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    action_id: str | None = Field(default=None, min_length=1)


class RunObservation(ContractModel):
    observation_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    summary: str = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    output_hash: str | None = None


class PlanProgress(ContractModel):
    goal_summary: str = Field(default="", max_length=2_000)
    completed_steps: list[str] = Field(default_factory=list, max_length=32)
    pending_steps: list[str] = Field(default_factory=list, max_length=32)
    blocked_reason: str | None = Field(default=None, max_length=1_000)


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


class PlannerInput(ContractModel):
    compiled_context: CompiledContext
    state_view: PlannerStateView
    tools: list[ToolSpec] = Field(default_factory=list)


class ToolCall(ContractModel):
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0)


class NextAction(ContractModel):
    action_type: ActionType
    tool_call: ToolCall | None = None
    final_answer: str | None = None
    rationale_summary: str | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "NextAction":
        if self.action_type is ActionType.TOOL_CALL:
            if self.tool_call is None or self.final_answer is not None:
                raise ValueError("tool_call 动作必须只包含 tool_call")
        elif self.action_type is ActionType.FINAL_ANSWER:
            if self.tool_call is not None or not self.final_answer:
                raise ValueError("final_answer 动作必须只包含非空 final_answer")
        return self


class ToolResult(ContractModel):
    action_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ResultStatus
    output: dict[str, Any] | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    error: RunError | None = None
    duration_ms: int | None = Field(default=None, ge=0)
```

实现时使用 `Field(default_factory=list)`，不要使用上述示例中的可变默认值；M1阶段 `NextAction` 只校验 `tool_call` 与 `final_answer` 两种动作，`ask_user` 在模块 5加入后再扩展并单独验收。`ToolResult.output` 只允许受控、可序列化的摘要数据，完整结果通过 `artifact_refs` 引用。

### 5.4 状态转换与恢复
新增纯函数 `transition_harness_state()` 和 `restore_harness_state()`，由 `LoopController` 唯一调用。M1 只冻结状态、身份和 checkpoint 恢复边界；确认回复的业务语义与 `confirmation_id` 对应关系留到暂停恢复模块。恢复允许两类非终态：`running` 用于进程中断或崩溃后的继续执行，`waiting_confirmation` 用于后续确认恢复；终态一律拒绝恢复。恢复必须校验 `user_id`、`conversation_id`、`thread_id`、`turn_id`、`run_id` 五个身份字段，并保留原 `original_goal`、`observations`、`plan_progress`。

终态不能从业务阶段直接写入。必须先从任意 `running/*` 进入 `running/finalization`，并在 `terminal_intent` 中记录目标终态；收尾成功后才写入 `completed`、`failed`、`cancelled` 或 `timeout`。非法转换归类为 `ErrorCategory.CONFLICT`，不得静默覆盖 checkpoint。

### 5.5 第一阶段研发任务和验收
1. 重构 `app/agent/harness/contracts.py`：补齐 schema version、确认 DTO、状态快照、终态意图、非空/范围校验和 JSON 序列化测试。
2. 重构 `app/agent/harness/state.py`：控制状态默认值、合法转换、running/waiting_confirmation 恢复边界、完整身份校验和 checkpoint 版本边界。
3. 保持并校验 `app/agent/state.py`：保留已有可选 `harness` 字段、旧业务字段和 `messages` reducer，验证旧图节点仍可按原字段读写。
4. 修改 `app/services/agent_service.py`：明确新 run 与 resume 的边界；具体 Harness 入口接入留到 Loop Controller 模块。
5. 验收：新建、合法/非法转换、终态保护、身份保留、JSON 往返、版本校验和旧图回归测试通过；暂停确认与 action 幂等不在 M1 单独验收。


## 6. 六个目标模块

### 模块 1：统一 Harness 运行状态层

> 本节是 M1 的最终设计基线。`app/agent/harness/` 当前仅有最小实现，以下契约优先于现有实验性代码；本节完成的是设计收口，不代表 M1 代码已经全部实现。

#### 1.1 模块职责

**一句话职责**：在不替换现有 `AgentState` 的前提下，统一保存一次 Harness Run 的可恢复控制状态。

**需要重构**：

- 定义运行状态、循环阶段、重试计数、计划进度、工具观察、受控错误和 checkpoint schema version。
- 规定新建 run、进程中断恢复和后续确认恢复使用不同入口，但共享同一 `thread_id`、`turn_id` 和 `run_id`。
- 规定控制字段由 Loop Controller 唯一写入，旧业务节点继续读写原有扁平 `AgentState` 字段。
- 在 checkpoint 边界校验嵌套 `harness` 数据，并拒绝未知版本、非法状态和身份不匹配。

**需要新增**：

- 在现有 Harness 契约中新增 `ConfirmationStatus`，并为缺失的 schema version、恢复引用和状态快照字段补齐明确 DTO 边界。

**不负责**：调用 LLM、选择工具、执行工具、构建 `CompiledContext`、保存会话最终结果、写长期记忆、实现 action 幂等或决定重试策略。M1 只冻结暂停相关 DTO 的结构，不实现暂停 API 和确认业务。

#### 1.2 输入与输出

| 项目 | 类型 | 必填 | 来源/去向 | 校验与边界 |
| --- | --- | --- | --- | --- |
| 新建请求 | `HarnessRequest` | 是 | API/AgentService -> 状态初始化 | `mode=new` 时必须有非空 `input_text`，身份由服务层注入 |
| 恢复请求 | `HarnessRequest` | 是 | API/AgentService -> 状态恢复 | `mode=resume` 时必须有完整 `HarnessRunRef` |
| 当前业务状态 | 现有 `AgentState` | 是 | `AgentService`/LangGraph Checkpointer | 保留原字段和 `messages` reducer，不整体替换 |
| 控制状态 | `HarnessControlState` | 新建时生成，恢复时读取 | `state.harness` | 通过 `HarnessStateSnapshot` 校验 |
| 恢复引用 | `HarnessRunRef` | resume 必填 | 请求 -> `restore_harness_state()` | 校验 `user_id`、`conversation_id`、`thread_id`、`turn_id`、`run_id` |
| 状态快照 | `HarnessStateSnapshot` | checkpoint 边界使用 | 状态层 -> `AsyncPostgresSaver` | JSON 可序列化；未知 schema version 拒绝 |

输出不是新的完整运行状态对象，而是写回现有 `AgentState` 的 `harness` 字段，并通过同一个 `AsyncPostgresSaver` 持久化。`PlannerStateView` 是给后续 Planning Agent 的受控投影，不得当作完整 checkpoint。

#### 1.3 与现有 AgentState 的组合关系

**现有能力**：`app/agent/state.py::AgentState` 是 `TypedDict(total=False)`；业务字段保持扁平，`messages` 使用 `Annotated[list[AnyMessage], add_messages]`。当前工作树已有 `harness: HarnessControlState` 字段。

**需要重构**：不创建复制全部业务字段的 `HarnessRunState`。组合关系固定为：

```text
HarnessGraphState
├── AgentState 的既有身份、消息和业务字段
└── harness: HarnessControlState
```

身份字段唯一来源仍是 `AgentState`。当前已验证 `app/services/agent_service.py::_new_identity()` 固定 `thread_id == conversation_id`，新请求创建 `turn_id` 和 `run_id`；resume 不得调用 `_new_identity()` 或 `_new_turn_state()` 清空现场。

#### 1.4 DTO 与 Protocol 契约

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


class HarnessRunRef(ContractModel):
    user_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class HarnessRequest(ContractModel):
    mode: Literal["new", "resume"]
    input_text: str | None = Field(default=None, min_length=1)
    run_ref: HarnessRunRef | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "HarnessRequest":
        if self.mode == "new" and not self.input_text:
            raise ValueError("new run 必须提供 input_text")
        if self.mode == "resume" and self.run_ref is None:
            raise ValueError("resume 必须提供 run_ref")
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
    artifact_refs: list[str] = Field(default_factory=list, max_length=32)
    output_hash: str | None = None


class ConfirmationRequest(ContractModel):
    confirmation_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    choices: list[str] = Field(default_factory=list, max_length=16)
    expires_at: datetime | None = None


class ConfirmationReply(ContractModel):
    confirmation_id: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4_000)
    resolved_conditions: dict[str, Any] = Field(default_factory=dict)


class HarnessControlState(TypedDict, total=False):
    schema_version: int
    status: str
    phase: str
    iteration: int
    max_iterations: int
    planner_retry_count: int
    context_retry_count: int
    tool_retry_counts: dict[str, int]
    last_context_build_id: str | None
    last_context_token_count: int | None
    terminal_intent: str | None
    original_goal: str
    plan_progress: dict[str, Any]
    observations: list[dict[str, Any]]
    resolved_conditions: dict[str, Any]
    pending_confirmation: dict[str, Any] | None
    last_error: dict[str, Any] | None


class HarnessStateSnapshot(ContractModel):
    schema_version: int = Field(ge=1)
    status: HarnessStatus
    phase: LoopPhase
    iteration: int = Field(ge=0)
    max_iterations: int = Field(gt=0)
    planner_retry_count: int = Field(ge=0)
    context_retry_count: int = Field(ge=0)
    tool_retry_counts: dict[str, int] = Field(default_factory=dict)
    last_context_build_id: str | None = None
    last_context_token_count: int | None = Field(default=None, ge=0)
    terminal_intent: Literal["completed", "failed", "cancelled", "timeout"] | None = None
    original_goal: str = Field(min_length=1)
    plan_progress: PlanProgress = Field(default_factory=PlanProgress)
    observations: list[RunObservation] = Field(default_factory=list)
    resolved_conditions: dict[str, Any] = Field(default_factory=dict)
    pending_confirmation: ConfirmationRequest | None = None
    last_error: RunError | None = None


class HarnessGraphState(AgentState, total=False):
    """现有 AgentState 加 Harness 控制字段的组合状态。"""

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


class CheckpointCodec(Protocol):
    def encode_harness(self, state: HarnessControlState) -> dict[str, Any]: ...
    def decode_harness(self, payload: dict[str, Any]) -> HarnessControlState: ...
```

`HarnessGraphState` 是对现有 `AgentState` 的目标组合类型说明，不是当前仓库已经导出的独立类型，也不是第二份运行时状态；M1 实现时应在现有状态边界中提供该组合类型，或使用等价的 TypedDict 组合。`CheckpointCodec` 只负责 `state.harness` 的校验和 JSON 编码，不负责调用或替代 `AsyncPostgresSaver`。`RunObservation` 使用当前代码已有的 `artifact_refs`，不引入尚未存在的 `result_ref` 或 `evidence_refs`。

#### 1.5 状态、阶段与转换规则

当前 `app/agent/harness/contracts.py` 已有 `HarnessStatus`：`running`、`waiting_confirmation`、`completed`、`failed`、`cancelled`、`timeout`；已有 `LoopPhase`：`start_run`、`restore_run`、`build_context`、`plan`、`validate_action`、`execute_tool`、`handle_tool_result`、`record_observation`、`wait_confirmation`、`finalization`。后续模块不得另造同义枚举。`ConfirmationStatus` 需要新增，值为 `not_required`、`pending`、`confirmed`、`rejected`，仅在暂停模块启用。

```text
absent -> running/start_run
running/* -> running/<next_loop_phase>
running/* -> waiting_confirmation/wait_confirmation
running/* -> running/finalization [terminal_intent = completed|failed|cancelled|timeout]
running/finalization -> completed/finalization
running/finalization -> failed/finalization
running/finalization -> cancelled/finalization
running/finalization -> timeout/finalization
waiting_confirmation/wait_confirmation -> running/restore_run
waiting_confirmation/wait_confirmation -> running/finalization [terminal_intent = cancelled]
terminal/finalization -> same terminal/finalization only
```

终态不能从业务阶段直接写入。必须先进入 `running/finalization`，由 `terminal_intent` 记录目标终态；只有 Finalization 成功后才落到 terminal status。收尾失败时保持 `running/finalization`，不得误报 `completed`。`waiting_confirmation` 不得调用 Planner 或 Tool Runtime；终态不得继续执行动作；计数不得为负；`original_goal` 不得在同一 run 中覆盖；同一终态重复提交只能返回幂等成功。

#### 1.6 初始化、更新、暂停和恢复

```text
start_new_run(request):
    assert request.input_text is not None
    identity = AgentService._new_identity(request.conversation_id)
    state = AgentService._new_turn_state()
    state["input_text"] = request.input_text
    state["user_id"], state["conversation_id"], state["thread_id"], \
    state["turn_id"], state["run_id"] = identity
    state["harness"] = new_harness_control_state(
        schema_version=current_version,
        status=running, phase=start_run, original_goal=request.input_text,
    )
    persist through the existing graph and AsyncPostgresSaver
    return state

update_control_state(state, patch):
    validate patch ownership and JSON-safe values
    apply only through state transition/update functions
    persist checkpoint before invoking the next external side effect
    return state

restore_run(request):
    ref = request.run_ref
    assert ref is not None
    state = load checkpoint by ref.thread_id
    verify state["user_id"] == ref.user_id
    verify state["conversation_id"] == ref.conversation_id
    verify state["thread_id"] == ref.thread_id
    verify state["turn_id"] == ref.turn_id
    verify state["run_id"] == ref.run_id
    harness = state["harness"]
    verify harness["schema_version"] is supported
    reject terminal status; allow running, running/finalization, or waiting_confirmation
    preserve original_goal, plan_progress, observations and business fields
    transition waiting_confirmation -> running/restore_run when applicable
    persist through the existing AsyncPostgresSaver
    return state

encode_harness(state):
    snapshot = HarnessStateSnapshot.model_validate(state["harness"])
    return snapshot.model_dump(mode="json")
```

M1 不把 `running` 恢复限定为 `waiting_confirmation`：进程可能在任意业务阶段或 `finalization` 阶段中断。确认 ID 的对应、过期和回答语义由后续暂停恢复模块校验；M1 只要求保留 `pending_confirmation` 的 JSON 边界。

#### 1.7 Checkpointer 边界与旧图兼容

**现有能力**：`app/clients/postgres_client.py` 已使用 `AsyncPostgresSaver.from_conn_string()`、`await checkpointer.setup()` 并将 Saver 传入 `build_agent_graph(checkpointer=checkpointer)`。

**设计约束**：

- `AsyncPostgresSaver` 继续保存完整 LangGraph 状态；不新增第二套 Checkpointer 表。
- `HarnessStateSnapshot` 只校验嵌套 `state.harness`，不保存 `CompiledContext`、完整工具结果、完整事件流、附件正文、SQL rows、Python 源码、数据库 session 或异常对象。
- 旧固定图继续读取扁平字段；M1 不删除 `build_agent_graph()`，也不要求旧业务节点理解 Harness 控制字段。
- 过渡期间必须明确同一个 `thread_id` 的唯一写入图；在默认入口切换前，不允许旧图和 Harness 图并发写同一 run。

#### 1.8 文件级任务、测试与验收

| 任务 | 类型 | 文件 | 产出 | 前置 |
| --- | --- | --- | --- | --- |
| M1.1 | 需要重构 | `app/agent/harness/contracts.py` | 补齐请求、状态快照、计划、观察、错误、确认 DTO 和 `ConfirmationStatus` | 无 |
| M1.2 | 需要重构 | `app/agent/harness/state.py` | 默认状态、状态/阶段校验、finalization 中间态、完整身份恢复和版本边界 | M1.1 |
| M1.3 | 需要重构 | `app/agent/state.py` | 保留旧字段和 reducer，声明可选 `harness` 组合关系 | M1.1/M1.2 |
| M1.4 | 需要重构 | `app/services/agent_service.py` | 分离新建与恢复，不让 resume 清空 turn 现场 | M1.2 |
| M1.5 | 需要新增 | `tests/test_harness_state.py` | DTO、默认容器、转换、身份、版本、checkpoint 和旧图兼容测试 | M1.1~M1.4 |

M1 验收标准：

1. 所有 DTO 的空值、范围、枚举、额外字段和 JSON 序列化规则有测试；默认列表/字典不跨 run 共享。
2. 业务阶段不能直接进入 terminal status；所有 terminal intent 先经过 `running/finalization`。
3. 恢复校验五个身份字段，并允许恢复 `running`、`running/finalization` 或 `waiting_confirmation`；终态拒绝恢复。
4. resume 保留原 `turn_id`、`run_id`、`thread_id`、目标、观察、计划进度和旧业务字段，不调用新 run 初始化逻辑。
5. `messages` reducer 和旧固定图构建行为不变；状态层不调用 LLM、ContextEngine、Tool Runtime、ConversationRepository 或 MemoryManager。
6. M1 设计完成不等于 M1 代码完成；实现和测试通过后，才允许进入 M2 的代码接入，但本文可以继续记录 M2 设计。

### 模块 2：ContextEngine 集成

> 本节是 M2 的文档设计基线。M2 只完成 Harness 到现有 ContextEngine 的集成契约，不实现 Planning Agent、Tool Runtime、Loop Controller 或暂停 API。

#### 2.1 模块职责

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

#### 2.2 已验证基线与增量结论

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

#### 2.3 输入 DTO 与校验

M2 的目标上游是 M1 的 `HarnessGraphState`、应用级 Agent 配置和已注入的 `ContextEngine`。当前仓库实际只有 `app/agent/state.py::AgentState`，因此在 M1 完成组合类型之前，M2 不得直接新增一个平行状态对象或假设该符号已经可导入。输入字段如下：

| 字段 | 类型 | 必填 | 来源 | 校验与边界 |
| --- | --- | --- | --- | --- |
| `user_id` | `str` | 是 | `HarnessGraphState.user_id` | 非空；只能由服务层提供，`runtime_context` 不能覆盖 |
| `conversation_id` | `str` | 是 | `HarnessGraphState.conversation_id` | 非空；与 Working loader 使用的会话/thread 映射一致 |
| `query` | `str` | 是 | `HarnessGraphState.input_text` | 非空；保留用户原问题，不能被工具摘要替换 |
| `system_instructions` | `str` | 是 | Agent 配置 | 只放系统行为约束，不拼接运行态 JSON |
| `agent_type` | `str` | 否 | Agent 配置 | 为空使用现有默认值 `general` |
| `project_id` | `str | None` | 否 | 请求/Agent 配置 | 只用于现有项目范围检索 |
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
    artifact_refs: tuple[ArtifactRef, ...] = Field(
        default_factory=tuple, max_length=32
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

#### 2.4 输出 DTO 与存储边界

输出仍然只有现有 `CompiledContext`：

| 输出字段 | 类型 | 下游 | Checkpointer | PostgreSQL | 说明 |
| --- | --- | --- | --- | --- | --- |
| `build_id` | `str` | Harness 状态、Planner 观测 | 只保存为 `last_context_build_id` | `context_build_runs.build_id` | 不保存完整上下文正文 |
| `messages` | `tuple[dict[str, Any], ...]` | `PlannerInput.compiled_context.messages` | 不保存 | 不直接保存正文 | 一次规划的临时模型输入 |
| `sections` | `ContextSections` | Planner/调试 | 不保存 | 不直接保存正文 | 复用现有语义分区 |
| `token_count` | `int` | Harness 状态、指标 | 可保存为 `last_context_token_count` | trace 审计 | 不得超过有效预算 |
| `resolved_asset_ids` | `tuple[str, ...]` | Planner/结果引用 | 不保存 | `context_build_runs.reference_resolution` | 只保留已校验的附件 ID，继承现有权限校验 |
| `trace` | `ContextBuildTrace` | 审计 | 不保存完整 trace | `context_build_runs` | 不包含候选正文 |

`CompiledContext` 本身不写入 checkpoint。恢复时重新读取 Checkpointer-backed Working Memory，并用恢复后的 Harness 状态重新 build。完整 SQL rows、Python 源码和大段工具输出只能由工具层通过已有 `artifact_refs` 传递，不能直接放入 `RuntimeContext`、`CompiledContext.trace` 或 Planner Prompt。

`RuntimeContext` 及其编译后的运行态消息也不写入 `context_build_runs`。现有 `ContextBuildTrace` 只保留 `build_id`、`query_hash`、检索计划摘要、引用解析、候选决策和 token 统计；Harness 通过 `last_context_build_id` 与审计行关联。M2 不新增运行态正文、原始条件或新的数据库列；如果后续需要运行态指纹，必须先单独定义脱敏哈希和迁移方案。

`PostgresContextStore.start_build()` 仍负责会话归属校验和创建 `pending` 审计行；`finish_build()` 仍只写入现有 trace 摘要；`fail_build()` 仍只写入受控错误说明。若 `start_build()` 在权限校验阶段失败，数据库中不会有对应的 `build_id` 行，集成层不得伪造“已开始构建”的审计记录；该失败直接映射为不可重试的权限错误。

运行态编译顺序固定为：

```text
system_instructions
    -> runtime background block
    -> existing evidence/background block
    -> Working Memory messages
    -> current query
```

`runtime background block` 使用独立的受控标记和明确的“仅作为运行数据读取”边界；不得拼接进 `system_instructions`，也不得覆盖现有 `ContextSections`。它虽然可能以 `system` role 传递给模型，但不是新的系统指令。`ContextCompiler.base_token_count()` 必须把 `system_instructions`、运行态块和当前 query 一起计入基础预算；`compile()` 的最终消息 token 数仍以现有 `TokenCounter.count_messages()` 的结果为准。

#### 2.5 字段级数据流

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
| Tool Runtime | `ToolResult.artifact_refs` | 映射为观察引用 | `RuntimeObservation.artifact_refs` | 下一次 build | 按引用读取结果，不复制结果 |
| `CompiledContext.messages` | 标准 role/content 消息 | 只读传入规划器 | `PlannerInput.compiled_context.messages` | Planning Agent | 生成下一步动作 |

当前代码没有 `ToolResult.result_ref` 或 `RunObservation.evidence_refs`；M2 使用已有 `artifact_refs`，不提前虚构字段。若 M4 决定拆分结果引用和证据引用，应在 M4 修改统一 DTO 后再同步本表。

`RuntimeContext` 不等于新的召回来源，也不替代 `ContextRetrievalPlan`。M2 默认只把运行态编译为补充背景；是否召回 Working、Semantic、Episodic、Perceptual 或 RAG 仍由现有 `ContextRequest` 策略、`ContextReferenceResolver` 和 `ContextPlanner` 决定。运行态中的工具摘要不得被当作长期记忆事实自动写回 Memory，也不得改变 `user_id`、`conversation_id`、`asset_ids` 的访问边界。

#### 2.6 与现有代码的衔接点

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

**暂不启用**：把 `RuntimeContext` 作为独立记忆类型、把运行态摘要自动写入长期 Memory、把 `result_ref`/`evidence_refs` 提前加入当前 DTO。

**暂不修改/暂不删除**：旧固定图、`build_agent_graph()`、Memory Formation、Query/Analysis/Report 节点和现有普通 Agent API；统一入口留到后续模块。保留现有 `build_context_engine()` 作为应用级装配入口，以及不传 `runtime_context` 的所有旧 `ContextRequest` 调用。M2 不删除旧代码，也不创建 `ContextEngineAdapter`。

**待验证**：LangGraph Checkpointer 对嵌套 `harness` 状态的实际 JSON 往返；运行态背景块在目标 ChatModel 中的消息角色和 token 计数；显式附件越权是否需要由 Resolver 抛错还是由 Harness 集成层统一升级；同一会话恢复时 Working loader 与 `conversation_id == thread_id` 的一致性。

#### 2.7 接口契约

以下接口使用 Protocol；接口只表达边界，不实现外部调用：

```python
from typing import Protocol

from app.agent.context_engine.contracts import CompiledContext, ContextRequest
from app.agent.harness.state import HarnessControlState
from app.agent.memory.enums import MemoryType
from app.agent.state import AgentState
from app.agent.harness.context_contracts import RuntimeContext

# M1 设计类型：当前仓库尚未导出独立的 HarnessGraphState。
class HarnessGraphState(AgentState, total=False):
    harness: HarnessControlState


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
        project_id: str | None = None,
        memory_types: tuple[MemoryType, ...] | None = None,
        enable_rag: bool = False,
        token_budget: int | None = None,
    ) -> ContextRequest: ...
```

`ContextBuilder` 仅用于单元测试替换现有 `ContextEngine`；生产对象仍是 `ContextEngine`。`ContextRequestFactory` 只做字段映射和 DTO 校验，不创建 PostgreSQL、Qdrant、Neo4j、RAG 或 LLM 客户端。构造错误归类为 `validation`；`ContextEngine.build()` 失败由 Harness 集成层转换为 `RunError(category=ErrorCategory.CONTEXT, ...)`，ContextEngine 本身不依赖 Harness 错误枚举。

#### 2.8 核心伪代码

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
        project_id=config.project_id,
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

#### 2.9 重建、预算与错误边界

| 事件 | 是否重新 build | 原因 |
| --- | --- | --- |
| 新建 run 进入首次规划 | 是 | 需要生成首个最新上下文快照 |
| 工具返回 `success` 或 `partial` 并记录观察 | 是 | 工具结果成为下一次规划的新证据 |
| 同一临时错误仍可安全重试 | 否 | 没有新增可规划事实，避免无意义构建 |
| 临时错误耗尽并准备重新规划 | 是 | 错误摘要已写入运行态 |
| 用户确认恢复 | 是 | `resolved_conditions` 发生变化 |
| Planner 仅输出格式错误并重试 | 否 | 只修复动作格式，不改变上下文事实 |
| ContextEngine 同一请求因可重试依赖失败 | 否 | 由 Loop Controller 重试同一请求并计数；校验和权限错误不得重试 |
| 进入 `running/finalization` | 否 | Finalization 不再需要规划上下文 |

预算规则：

1. 有效预算来自 `request.token_budget` 或现有 `ContextPolicy.max_context_tokens`。
2. 系统指令、运行态背景消息和当前 query 均属于基础输入，必须在候选召回前计数；基础输入超预算时不使用空上下文继续调用 Planner。
3. 运行态投影先按“原始目标 -> 已确认条件 -> 计划摘要 -> 最新观察 -> 最新错误”保留和裁剪；完整结果只保留 `artifact_refs`。
4. 现有候选仍由 `ContextSelector` 和 `TokenBoundaryCompressor` 按已有策略选择和压缩；M2 不复制一套 selector。
5. `runtime_context=None` 时不得增加运行态背景消息，确保现有 ContextEngine 测试和普通调用行为不变。

错误边界：

- `ContextEngine._validate_request()` 当前在生成 `build_id` 和调用 `start_build()` 之前对空身份、空 query、非法预算和非法附件 ID 抛出 `ValueError`；这类失败当前没有 `context_build_runs` 审计行，M2 将其映射为 `validation`，不可重试。
- `PostgresContextStore._ensure_conversation_access()` 当前在 `start_build()` 阶段抛出 `PermissionError`；它发生在现有 `ContextEngine.build()` 的主 `try` 之前，M2 必须把建构启动和失败分类纳入同一集成边界，且不得把该错误重试为上下文构建。
- 当前附件解析器会把不存在或无权访问的附件放入 `unresolved_references` 并继续构建。M2 必须区分“请求显式提供的 `asset_ids`”与“历史自然语言引用”：前者的无权访问应转换为不可重试的 `permission` 错误，后者仍可保留为受控未解析引用并交给后续澄清流程。
- 临时记忆、附件或 RAG 依赖不可用，可由 Loop Controller 按配置重试；ContextEngine 不自行重试。
- 当前 `ContextEngine.build()` 已记录应用日志、调用 `ContextStore.fail_build()` 并重新抛出原异常；M2 保留该行为。
- 集成层只保存 `ErrorCategory`、稳定错误码、脱敏消息和 retryable 标志，不保存异常对象、完整堆栈或原始 prompt。

#### 2.10 文件级任务与测试验收

| 任务 | 类型 | 文件 | 产出 | 前置 |
| --- | --- | --- | --- | --- |
| M2.1a | 需要新增 | `app/agent/harness/context_contracts.py` | 新增 `RuntimeContext`、`RuntimeObservation`、`RuntimeErrorSummary`、`RuntimePlanProgress` | M1 契约 |
| M2.1b | 需要重构 | `app/agent/context_engine/contracts.py` | 在现有 `ContextRequest` 末尾增加 `runtime_context: RuntimeContext | None = None`；不创建第二套请求或输出 DTO | M2.1a |
| M2.2 | 需要新增 | `app/agent/harness/context_service.py` | 状态到请求的纯 DTO 投影，不创建 Engine Adapter | M2.1b |
| M2.3 | 需要重构 | `app/agent/context_engine/compiler.py` | 运行态背景消息、隔离标记和真实 token 计数 | M2.1b |
| M2.4 | 需要重构 | `app/agent/context_engine/engine.py` | 基础预算包含运行态；统一 `start_build()`、请求校验、权限错误、显式附件越权和失败审计边界 | M2.3 |
| M2.5a | 需要新增 | `tests/test_harness_context.py` | 状态投影、身份隔离、裁剪、恢复重建和大对象排除测试 | M2.1a~M2.4 |
| M2.5b | 需要重构 | `tests/test_context_engine.py` | `runtime_context=None` 旧行为回归、运行态消息和预算测试 | M2.1b~M2.4 |

**单元测试**：

- `RuntimeContext` 的 extra forbid、空目标、字段长度、列表上限、JSON 往返和默认容器隔离。
- `RuntimeContext` 不得携带身份字段；`resolved_conditions` 只能接收有界字符串，未确认条件和任意嵌套对象必须被拒绝或裁剪。
- `HarnessControlState -> RuntimeContext` 的映射；未确认的 `pending_confirmation` 不得进入 `resolved_conditions`。
- `ContextRequest.runtime_context=None` 时现有测试全部保持；有运行态时只增加受控背景消息，不改变系统指令。
- 系统指令、运行态消息和 query 一起计入预算；基础输入超预算时不调用后续 Planner。
- `ToolResult.artifact_refs -> RuntimeObservation.artifact_refs`，不读取或生成当前不存在的 `result_ref/evidence_refs`。

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

### 模块 3：Planning Agent

新增 `app/agent/harness/planning.py`、`action_validator.py` 和 `prompts/plan_next_action.prompt`。输入是 `CompiledContext`、`PlannerStateView`、ToolSpec 列表；输出结构化 `NextAction`，支持 `tool_call`、`ask_user`、`final_answer`。解析失败、未知工具、参数不匹配和超权限进入受控错误和有限重试。Planner 不访问数据库、不执行工具、不写 Memory、不保存隐藏思考。

### 模块 4：Tool Runtime

新增 `app/agent/harness/tools/contracts.py`、`registry.py`、`runtime.py`。`data_catalog` 复用现有 Meta/Qdrant/ES；`query_data` 是目标工具名而非现有函数，内部复用 `query_graph`；`analyze_data` 复用 `execute_analysis` 和 Sandbox；`build_report` 复用报告计划和真实绑定。`knowledge_base` **暂不启用**。只有跨旧图和 Harness 共享的纯业务逻辑才提取 Service，不能创建薄字段复制 Adapter。

### 模块 5：Loop Controller

新增 `app/agent/harness/loop_controller.py`、`runner.py`；修改 `app/api/routers/agent.py`、`app/services/agent_service.py`。主循环为 `Start/Restore -> BuildContext -> Plan -> ValidateAction`；tool_call 进入执行、记录观察和下一次 build；ask_user 进入 `Pause/Checkpoint -> waiting_confirmation`；resume 校验身份、会话、run 和 confirmation_id 后重新 build；final_answer 进入 Finalization。

同一 action 的安全重试与重新规划是不同路径；成功 action 通过 action_id 幂等查询避免重放。建议各类重试 2 次、max_iterations 8、run_timeout 5 分钟，但必须接入 Settings/config.yaml，不可写死。

### 模块 6：Finalization + Memory Formation

新增 `app/agent/harness/finalization.py`；修改 `app/services/agent_service.py` 和 `ConversationRepository`。复用 `build_turn_output()`，只保存受控最终输出。当前 `finish_turn()` 会清 active run 并写 completed_at，不能直接用于 waiting；需增加 pause/resume 状态契约和幂等键。正确链路为 `Finalization -> ConversationRepository -> Checkpoint -> MemoryFormationService.submit(TurnMemoryInput)`，Formation 失败不能覆盖已保存答案。

## 7. 接口、存储、暂停和可观测性

建议用 Protocol：`PlanningAgent.plan(PlannerInput) -> NextAction`、`Tool.execute(ToolCall) -> ToolResult`、`ToolRuntime.execute(ToolCall) -> ToolResult`、`FinalizationService.finalize(FinalizationInput) -> FinalizationResult`。字段流为 `NextAction -> ToolRuntime -> ToolResult -> RunObservation -> RuntimeContext -> CompiledContext`，确认回复进入 `resolved_conditions`，最终结果进入 API/SSE，FinalizationInput 进入 `MemoryFormationService.submit()`。

| 数据 | Checkpointer | PostgreSQL | Qdrant | Neo4j |
| --- | --- | --- | --- | --- |
| 可恢复 Agent/Harness 状态 | 是 | 可选审计 | 否 | 否 |
| 会话/轮次/最终消息 | 图消息可有 | conversations、conversation_turns、conversation_messages、turn_outputs | 否 | 否 |
| Working Memory | AgentState.messages | 否 | 否 | 否 |
| 长期记忆事实 | 否 | agent_memories、sources、assets | 否 | 否 |
| 记忆向量投影 | 否 | 同步状态/失败记录 | 是 | 否 |
| Semantic 实体关系 | 否 | memory_graph_projections | 否 | 是 |
| Context 摘要/Trace | 否 | context_conversation_summaries、context_build_runs | 否 | 否 |
| 工具结果 | 仅摘要/引用 | 后续 Artifact/审计表 | 否 | 否 |

保留 `POST /api/agent/run` 和 `/run/stream`，新增确认、运行状态、取消接口，二者共享 Loop Controller。SSE 事件统一为 `run.started/context.built/planner.completed/tool.started/tool.completed/run.paused/run.resumed/run.completed/run.failed`；不由旧 AgentService 和 Harness 各自保存历史事件。指标标签只用低基数 status、phase、tool_name、error_category，不使用用户 ID、run ID、turn ID、conversation ID 或原文。日志只保存脱敏摘要、引用、哈希和错误分类。

## 8. Meta RAG、暂停、异常和幂等

Meta/Data Catalog RAG 只覆盖指标、表、字段、维度、维度值及关系；企业知识库覆盖制度、业务规则、口径文档和分析规范。当前没有可靠独立企业知识库，标记为**暂不启用**。

暂停流程：`ask_user -> ConfirmationRequest -> waiting_confirmation -> Checkpoint -> 鉴权恢复 -> resolved_conditions -> 重新 build`。覆盖口径冲突、时间范围缺失、历史/附件引用不唯一及工具 needs_user。临时错误才重试；用户输入不足和口径冲突暂停；不可恢复错误进入 Finalization。成功动作按 `(run_id, action_id)` 幂等；报告按 `(turn_id, output_type)` 幂等；Formation 需增加 turn/run 幂等键。

## 9. 依赖装配、开发顺序和测试

装配顺序：PostgreSQL SessionFactory -> AsyncPostgresSaver -> MemoryRuntime -> ContextEngine -> 必要共享 Service -> ToolRegistry/Runtime -> PlanningAgent -> LoopController -> Harness Runner -> AgentService -> FastAPI/SSE。客户端、Saver、MemoryRuntime、ContextEngine、Registry 为应用级；仓储、Service、Run 上下文为请求级；模块内部不得创建外部客户端。

增量开发顺序固定为六个模块：M1 统一 Harness 运行状态层；M2 ContextEngine 集成；M3 Planning Agent；M4 Tool Runtime；M5 Loop Controller；M6 Finalization + Memory Formation。每个模块先完成对应文档契约和单元测试，再进入实现与集成测试；M5 启用暂停恢复前必须具备幂等 action 和可恢复 Checkpoint。API/SSE 装配、旧图迁移、可观测性和清理属于 M5/M6 的交付收口，不另立模块。

单元测试覆盖 DTO/枚举、状态转换、RuntimeContext、Planner 合法/非法输出、ToolSpec/参数/权限、结果归一化、错误分类、重试和幂等。集成测试覆盖 MemoryManager、Checkpointer Working loader、Query/Analysis/Report 工具、ConversationRepository、MemoryFormationService、暂停恢复和成功工具不重复执行。端到端覆盖普通问数、复杂多步分析、口径冲突、确认继续、临时错误、Planner 失败、不可恢复错误、超时、取消、同步、SSE 和旧图兼容。

## 10. 待验证事项与本期明确不做

待验证：LangGraph 当前版本下嵌套 HarnessControlState 的序列化；同一会话是否允许多个 active run；恢复时 turn_id 是否保持；分析并行任务的 Session 安全；DW 只读 SQL、statement timeout、结果上限；Artifact 存储介质；新旧图共用 thread_id 时的唯一写入来源。

本期不实现微信登录/手机号授权、支付/退款/提现、企业知识库、通用 Python 执行器；不删除旧固定图；不把目标工具名误写成当前已有函数；不把建议默认值写成已验证事实。
