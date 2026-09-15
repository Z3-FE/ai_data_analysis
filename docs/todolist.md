# Data Agent Harness 纵向切片 TodoList

增量技术文档：docs/data_agent_harness_incremental_sdd.md

## 当前真实进度（2026-09-15）

- [x] A：最小 Harness 闭环已完成并通过聚焦测试。
- [x] B：真实 Planning Agent、动作提交和状态持久化已完成并通过聚焦测试。
- [x] C：真实 `query_data` Tool Runtime 循环、结果引用和上下文重建已完成并通过聚焦测试。
- [x] D：PostgreSQL 运行状态持久化、`ask_user` 暂停、确认消费、同一 `run_id` 恢复和重复确认保护已完成。
- [x] D：运行级 deadline 和取消收口已有确定性测试；客户端断流已用真实 HTTP 验证为 `cancelled/finalization`，并释放 `active_run_id`。
- [x] D：真实 PostgreSQL 的运行级 deadline 验收已完成（真实 HTTP 前端验收）。
- [x] D：Finalization Ledger（`harness_finalizations`）+ 固定顺序收口 + `reconcile()` 已完成；真实 PostgreSQL 崩溃矩阵验收通过。
- [x] E：真实 `PostgresFinalizationService`（历史 → 终态 checkpoint → 释放 active_run → Memory Formation → 账本完成）已接入主链；`tests/test_harness_vertical_e.py` 用真实 PostgreSQL 通过；全量 HTTP 跨基础设施统一验收仍待完成。
- [x] D6：接入 `analyze_data` 高层工具，已通过 Harness 聚焦测试。
- [x] D7：真实 HTTP 验证 `query_data` + `analyze_data` 循环；两条链路均完成 Planner、工具执行、Artifact、Observation、上下文重建和最终收口。
- [x] D8：接入 `build_report` 高层工具，并把 `RenderedReport` 按引用收口为 `output_type="rendered_report"`；`tests/test_harness_d8_report.py` 通过。

D8 实现说明：

- `BuildReportTool` 只读上游 Artifact（`query_result` / `analysis_result`），不重新查数也不自己写 Artifact；报告本体的持久化由 `ToolRuntime` 按 `ToolSpec.result_kind="report"` 完成。
- 入参只有 `goal` / `result_refs` / `title_hint`；报告结构由 `generate_report_plan` 从真实证据推导，不让 Planner 补指标名。
- 上游结果统一归一化成 `execution_mode="analysis"` 形态后直接复用 `generate_report_plan` + `render_report`，两个节点一行未改；只输出 `RenderedReport`，不做 markdown/csv 多格式。
- `FinalizationInput` 新增 `final_output_type` / `final_output_ref` 两个小字段；报告本体不进账本 `input_payload`，由 `_save_history()` 按引用取回写入 `turn_outputs`，reconcile 走同一路径。
- `LoopController._final_output()` 按 `ToolSpec.result_kind == "report"` 回溯 `observations`，不按工具名判断；以后新增报告类工具不需要改控制器。
- Memory Formation 只透传 `output_type`，`output_payload` 仍只放 `{"message": final_answer}`；完整报告进记忆提取只会稀释信号。
- 待做：`build_report` 的真实 HTTP 验收（`query_data` → `build_report` 与 `analyze_data` → `build_report` 两条链路），以及前端 `rendered_report` 渲染确认。

Finalization 对账设计要点（已实现）：

- `PostgresFinalizationService.finalize()` 按固定顺序推进账本：`prepared → history_saved → checkpoint_saved → released → formation_submitted → completed`；每一步操作本身幂等，账本锁定 `finalization_digest` 与 `input_payload`。
- 收口失败抛出 `FinalizationFailure`（运行保持 `running/finalization`，HTTP 503），控制器与 API 都不做 FAILED 降级；`POST /api/harness/run/reconcile`（body：`run_id` + `user_id`）从账本当前阶段重放，结构上不可能回到 Planner、Tool Runtime 或 ContextEngine。
- Formation 提交失败会阻塞终态（账本停在 `released`），reconcile 依赖 `formation_key` 幂等重试；后台形成任务保持 PENDING 语义。

暂停/恢复重执行语义（2026-09-15 前端验收发现）：

- `ask_user` 暂停后，只有 Harness 状态（run snapshot、confirmation、checkpoint）跨暂停存活；工具内部部分进度不进 checkpoint，确认后 `resume()` 在同一 `run_id` 上重新 BuildContext、重新规划并重跑工具（`action_seq` 递增），第一次的工具执行结果被丢弃。
- 待优化（工具侧）：`analyze_data` 等重工具在真正开跑前先做参数歧义校验（如时间范围不明确直接返回 partial + 澄清请求），避免执行 2 分钟以上才发现需要 ask_user。
- 待优化（Planner 侧）：对“近N个月”这类相对时间，Planner 应优先基于上一轮时间范围主动澄清，而不是等工具跑完返回 partial 后再 ask_user。

最近一次真实 HTTP 验收记录：

- `query_data`：运行 `18c257ad-7da9-4f1f-901f-148f5ab46ef1`，最终为 `completed/finalization`，包含 1 个成功 Artifact 和 1 个成功 Observation。
- `analyze_data`：运行 `d0fd9c75-88e0-4fdf-9905-fc12e0802207`，最终为 `completed/finalization`，包含 1 个成功分析 Artifact 和 1 个成功 Observation。
- 断流：运行 `5373dddb-8a93-4b5d-af6c-d65624b42263`，流式进行中关闭页面后落为 `cancelled/finalization`；账本 `completed`、无 formation 提交，`active_run_id` 已释放。
- deadline：`run_timeout_seconds: 20` 下运行 `4aef9378-8c67-4e4a-b9d7-60f22496d9d6` 落为 `timeout/finalization`；账本 `completed`、无 formation 提交，`active_run_id` 已释放；真实 PostgreSQL 未复现 aware/naive 时区问题。
- Harness 聚焦回归：33 passed，1 个现有 Starlette/httpx 弃用警告；新增 Harness SSE 空闲心跳验收。

当前边界：`/api/harness/run` 是正式入口，Harness 是唯一主线；不兼容旧 Graph Agent，不恢复 `WorkingStateLoader`、`create_working_state_loader` 或旧图相关测试。

## 0. 当前基线与冲突记录

### 已读取的现有代码


| 范围                 | 当前事实                                                                                                                                                                    | 状态                                      |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| Harness 状态         | `app/agent/harness/contracts.py`、`app/agent/harness/state.py`已存在`HarnessStatus`、`LoopPhase`、`HarnessRunRef`、`HarnessStateSnapshot`、`NextAction`、`ToolResult`等契约 | 现有能力，M1 已冻结                       |
| LangGraph 状态       | `app/agent/state.py::AgentState`保留旧业务字段和`messages`reducer；`HarnessGraphState`当前位于`app/agent/state.py`                                                          | 现有能力                                  |
| ContextEngine        | `app/agent/context_engine/engine.py::ContextEngine.build()`已实现；`ContextRequest`已包含`runtime_context`                                                                  | M2 部分具备，尚未形成 LoopController 闭环 |
| Harness Context 投影 | `app/agent/harness/context_contracts.py`、`app/agent/harness/context_service.py`已存在`RuntimeContext`、`HarnessContextRequestFactory`、`HarnessRuntimeContextProjector`    | M2 部分具备                               |
| Planning Agent       | 未发现统一的`PlanningAgent`                                                                                                                                                 | 需要新增                                  |
| ActionCommitter      | 未发现`ActionCommitter`、prepared/checkpoint/committed 持久化实现                                                                                                           | 需要新增                                  |
| Tool Runtime         | 未发现统一`ToolRuntime`、`ToolRegistry`、`ToolExecutionStore`                                                                                                               | 需要新增                                  |
| LoopController       | 未发现 Harness 级`LoopController`                                                                                                                                           | 需要新增                                  |
| Finalization         | `app/services/agent_service.py`和`app/agent/nodes/finalize_turn.py`分散承担收尾逻辑                                                                                         | 需要重构                                  |
| Memory Formation     | `app/agent/memory/formation_service.py::MemoryFormationService.submit()`已存在，但没有稳定`formation_key`幂等                                                               | 需要重构                                  |
| Checkpointer         | `app/clients/postgres_client.py`使用`AsyncPostgresSaver`，并接入现有固定图                                                                                                  | 现有能力，需要复用                        |
| Query 能力           | `app/agent/query_graph.py::query_graph`是可复用 LangGraph 子图，没有独立`query_data()`函数                                                                                  | 现有能力，需要通过工具封装                |
| Agent 入口           | `app/services/agent_service.py::_run_async()`、`qyStream()`直接调用旧图                                                                                                     | 需要在最终切片后迁移                      |

### 文档与代码的已知差异

1. 文档原有研发顺序是 M1～M11，当前任务改为 A～E 五个纵向切片。后续执行以 A～E 为准，M1～M6 继续表示架构职责。
2. 文档中的 `HarnessGraphState` 实际位于 `app/agent/state.py`，不位于 `app/agent/harness/contracts.py`。
3. M2 的 `runtime_context`、状态投影代码已经存在，但尚未被 `LoopController.start()` 调用，因此不能视为 M2 已完成。
4. `ConversationRepository.start_turn()` 当前会直接设置 `active_run_id`，尚未拒绝其他非终态运行。
5. 当前 `MemoryFormationService.submit()` 每次生成新的 `formation_run_id`，不能保证重复提交幂等。
6. 当前旧图是一次性固定图，没有 Harness 内部回边、暂停恢复或动作提交协调。
7. 当前没有可直接复用的 `query_data()`、`analyze_data()`、`build_report()` 函数；这些名称只能作为目标工具名称。

---

## 1. 切片划分总览


| 切片 | 端到端主链                                                                                   | 真实模块                                                                                   | 假模块                                     | 主要新增能力                 |
| ---- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------ | ---------------------------- |
| A    | `start -> build_context -> plan -> finalization -> result`                                   | ContextEngine、M1 状态层                                                                   | PlanningAgent、Finalization、测试 RunStore | 最小可跑闭环                 |
| B    | `start -> context -> real plan -> commit -> dispatch -> result`                              | ContextEngine、M1、PlanningAgent、ActionCommitter                                          | Tool Runtime、暂停处理、Finalization       | 真 Planning Agent 和动作提交 |
| C    | `start -> plan -> commit -> ToolRuntime -> observation -> rebuild context -> plan -> result` | ContextEngine、PlanningAgent、ActionCommitter、QueryDataTool                               | 无业务工具假实现；测试中可替换外部依赖     | 第一条真实工具循环           |
| D    | `start -> ask_user -> checkpoint -> waiting -> resume -> context -> plan -> result`          | ContextEngine、PlanningAgent、Checkpointer、ConfirmationStore                              | Finalization、部分持久化 RunStore          | 暂停与同一 run 恢复          |
| E    | `start -> context -> plan -> finalization -> history/checkpoint/formation -> result`         | ContextEngine、PlanningAgent、Finalization、MemoryFormationService、ConversationRepository | 仅测试环境中的外部基础设施替身             | 完整收口和记忆形成提交       |

### 架构模块与切片映射


| 架构模块           | A        | B          | C                      | D          | E              |
| ------------------ | -------- | ---------- | ---------------------- | ---------- | -------------- |
| M1 统一状态层      | 使用     | 使用       | 使用                   | 使用       | 使用           |
| M2 ContextEngine   | 真实接入 | 真实接入   | 结果驱动重建           | 恢复后重建 | 最终收口前读取 |
| M3 Planning Agent  | Fake     | 真实       | 真实                   | 真实       | 真实或测试替身 |
| M4 Tool Runtime    | 不启用   | Fake       | QueryDataTool 真实接入 | 延续 C     | 延续 C         |
| M5 Loop Controller | 最简版   | 加动作提交 | 加工具循环             | 加暂停恢复 | 完整生命周期   |
| M6 Finalization    | Fake     | Fake       | Fake                   | Fake       | 真实接入       |

---

## 2. 切片 A：最小可跑闭环

### 本刀目标

使用真实 ContextEngine，先用 FakePlanningAgent 和 FakeFinalization 跑通从 `LoopController.start()` 到最终结果的最小闭环。

### 需要先定义的 DTO / Protocol


| 文件路径                              | 名称                       | 说明                                                    |
| ------------------------------------- | -------------------------- | ------------------------------------------------------- |
| `app/agent/harness/loop_contracts.py` | `StartRunCommand`          | 新建运行所需的`HarnessRunRef`、原始输入、项目和附件范围 |
| `app/agent/harness/loop_contracts.py` | `LoopRunResult`            | A 阶段只支持 completed/failed 结果                      |
| `app/agent/harness/loop_contracts.py` | `ContextBuilder`Protocol   | 约束`build(ContextRequest) -> CompiledContext`          |
| `app/agent/harness/loop_contracts.py` | `PlanningPort`Protocol     | 约束基于`PlannerInput`返回`NextAction`                  |
| `app/agent/harness/loop_contracts.py` | `FinalizationPort`Protocol | A 阶段的假收口端口                                      |
| `app/agent/harness/loop_contracts.py` | `HarnessRunStore`Protocol  | A 阶段只需要创建、读取和保存最小运行状态                |

M1 已冻结的 `HarnessControlState`、`HarnessRunRef`、`HarnessStateSnapshot` 不修改，只读使用。

### 需要新增的文件

* `app/agent/harness/loop_contracts.py`
* `app/agent/harness/loop_controller.py`
* `tests/fakes/harness/fake_planning_agent.py`
* `tests/fakes/harness/fake_finalization.py`
* `tests/fakes/harness/fake_run_store.py`
* `tests/test_harness_vertical_a.py`

### 需要修改的文件


| 文件路径                               | 函数或位置                              | 修改内容                                             |
| -------------------------------------- | --------------------------------------- | ---------------------------------------------------- |
| `app/agent/harness/__init__.py`        | 导出列表                                | 导出 A 阶段正式 DTO 和 Protocol                      |
| `app/agent/harness/loop_controller.py` | `LoopController.start()`                | 实现`start -> build_context -> plan -> finalization` |
| `app/agent/harness/context_service.py` | `HarnessContextRequestFactory.create()` | 仅在 E2E 测试发现字段映射缺口时做最小修正            |
| `app/agent/context_engine/engine.py`   | `ContextEngine.build()`                 | 不改业务逻辑，仅确认真实实现可被注入调用             |

### Fake 模块清单


| Fake 模块                                    | 替换时机                    | 真实实现                                                  | 替换点                                             |
| -------------------------------------------- | --------------------------- | --------------------------------------------------------- | -------------------------------------------------- |
| `tests/fakes/harness/fake_planning_agent.py` | 切片 B                      | `app/agent/harness/planning.py::PlanningAgent`            | `LoopController`的`planning_agent`依赖注入点       |
| `tests/fakes/harness/fake_finalization.py`   | 切片 E                      | `app/agent/harness/finalization.py::FinalizationService`  | `LoopController`的`finalization_service`依赖注入点 |
| `tests/fakes/harness/fake_run_store.py`      | 切片 D/E 的持久化实现完成后 | `app/agent/harness/run_store.py::PostgresHarnessRunStore` | `LoopController`的`run_store`依赖注入点            |

每个 Fake 模块的模块级 docstring 必须写明：

* 当前只服务哪个切片；
* 将在哪个切片被替换；
* 替换为哪个真实路径；
* 在 LoopController 的哪个依赖注入点替换。

### 验收测试

* 文件：`tests/test_harness_vertical_a.py`
* 测试名：`test_slice_a_start_to_result`
* 唯一验收标准：从 `LoopController.start()` 开始，真实 `ContextEngine.build()` 成功，FakePlanningAgent 返回 `final_answer`，FakeFinalization 返回 `LoopRunResult(status=completed)`，全链路通过。

### 本刀红线

* 不实现真实 PlanningAgent。
* 不实现 Tool Runtime。
* 不进入 `query_graph`。
* 不实现暂停、恢复、取消和超时。
* 不调用真实 M6 Finalization。
* 不修改 M1 冻结 DTO 和状态转换。
* `action_seq=1` 只能作为 A 阶段测试动作载荷，不得被当作完整动作提交协议。
* 不接入 `AgentService` 默认入口，避免 A 阶段替换旧图行为。

---

## 3. 切片 B：接真 Planning Agent

### 本刀目标

将 FakePlanningAgent 替换为真实结构化 PlanningAgent，并完成 `prepared -> checkpoint -> committed` 动作提交；工具仍使用 Fake。

### 需要先定义的 DTO / Protocol


| 文件路径                               | 名称                       | 说明                                                     |
| -------------------------------------- | -------------------------- | -------------------------------------------------------- |
| `app/agent/harness/planning.py`        | `PlanningAgent`Protocol    | 接收`PlannerInput`，返回经过 Pydantic 校验的`NextAction` |
| `app/agent/harness/action_commit.py`   | `ActionCommitRequest`      | 包含 run identity、候选序号、动作摘要和 digest           |
| `app/agent/harness/action_commit.py`   | `ActionCommitResult`       | 表示 prepared/checkpoint/committed 协调结果              |
| `app/agent/harness/action_commit.py`   | `ActionCommitter`Protocol  | 约束动作准备、提交和恢复对账                             |
| `app/agent/harness/tools/contracts.py` | `ToolExecutionRequest`     | 约束已提交`ToolCall`的执行身份和阶段                     |
| `app/agent/harness/tools/contracts.py` | `ToolRuntime`Protocol      | B 阶段使用 Fake 实现，真实实现留到 C                     |
| `app/agent/harness/loop_contracts.py`  | `ActionDispatcher`Protocol | 统一分派`tool_call`、`ask_user`、`final_answer`          |

DTO 继续复用 M1 的 `NextAction`、`ToolCall`、`ToolResult`、`ToolSpec`，不重新定义同名类型。

### 需要新增的文件

* `app/agent/harness/planning.py`
* `app/agent/harness/action_commit.py`
* `app/agent/harness/tools/contracts.py`
* `tests/fakes/harness/fake_tool_runtime.py`
* `tests/fakes/harness/fake_confirmation_dispatcher.py`
* `tests/test_harness_vertical_b.py`

### 需要修改的文件


| 文件路径                               | 函数或位置                                 | 修改内容                                                   |
| -------------------------------------- | ------------------------------------------ | ---------------------------------------------------------- |
| `app/agent/harness/loop_controller.py` | `start()`、`_plan()`、`_dispatch_action()` | 接入真实 PlanningAgent 和动作提交                          |
| `app/agent/harness/loop_controller.py` | `_commit_action()`                         | 实现 prepared、checkpoint、committed 顺序                  |
| `app/agent/harness/__init__.py`        | 导出列表                                   | 导出 Planning、ActionCommit 和 Tool Runtime 契约           |
| `app/agent/harness/context_service.py` | Planner 输入构造位置                       | 将真实`CompiledContext`传给`PlannerInput.compiled_context` |
| `app/agent/harness/contracts.py`       | 不修改                                     | 仅验证当前 M1 DTO 能满足 B 的输入                          |

### Fake 模块清单


| Fake 模块                                             | 替换时机 | 真实实现                                                     | 替换点                                 |
| ----------------------------------------------------- | -------- | ------------------------------------------------------------ | -------------------------------------- |
| `tests/fakes/harness/fake_tool_runtime.py`            | 切片 C   | `app/agent/harness/tools/runtime.py::ToolRuntime`            | `LoopController`的`tool_runtime`注入点 |
| `tests/fakes/harness/fake_confirmation_dispatcher.py` | 切片 D   | `app/agent/harness/confirmation_store.py::ConfirmationStore` | `ask_user`分派点                       |
| `tests/fakes/harness/fake_finalization.py`            | 切片 E   | `app/agent/harness/finalization.py::FinalizationService`     | `final_answer`收口点                   |

B 阶段的 `ask_user` 只验证动作类型能够被识别和分派，不实现持久暂停。测试替身可以立即返回一个已确认的结果，但必须在 docstring 中标明切片 D 将替换该行为。

### 验收测试

* 文件：`tests/test_harness_vertical_b.py`
* 测试名：`test_slice_b_start_to_result_dispatches_actions`
* 唯一验收标准：从 `LoopController.start()` 到返回结果，真实 PlanningAgent 输出可以被 Pydantic 解析；连续动作的 `action_seq` 单调递增；`tool_call`、`ask_user`、`final_answer` 三类动作均经过 ActionCommitter 并被正确分派；全链路通过。

### 本刀红线

* Fake Tool 不得连接真实数据库。
* PlanningAgent 不得直接调用 Tool Runtime。
* ActionCommitter 不得宣称与 `AsyncPostgresSaver` 和业务数据库共享一个事务。
* `prepared` 动作不能直接交给 Tool Runtime。
* 不实现真实暂停、恢复、ConfirmationStore 和 API。
* 不修改 M1 DTO。
* 不允许 Planner 自行生成或覆盖正式 `action_id`。
* Planner 失败重试不能增加已提交的 `action_seq`。

---

## 4. 切片 C：接真 Tool Runtime

### 本刀目标

将 Fake Tool 替换为真实 Tool Runtime，首先接入真实 `QueryDataTool`，并完成一次以上的结果驱动循环。

### 需要先定义的 DTO / Protocol


| 文件路径                                | 名称                          | 说明                                   |
| --------------------------------------- | ----------------------------- | -------------------------------------- |
| `app/agent/harness/tools/contracts.py`  | `QueryDataInput`              | 查询问题、项目范围、附件范围和结果限制 |
| `app/agent/harness/tools/contracts.py`  | `ToolHandlerResult`           | M4 内部工具处理结果，不跨出 M4         |
| `app/agent/harness/tools/contracts.py`  | `ToolExecutionStore`Protocol  | 约束动作执行记录和幂等查询             |
| `app/agent/harness/tools/contracts.py`  | `ResultArtifactStore`Protocol | 约束大结果外置和引用生成               |
| `app/agent/harness/tools/registry.py`   | `ToolRegistry`Protocol        | 约束工具注册、启用状态和`list_specs()` |
| `app/agent/harness/tools/query_data.py` | `QueryDataTool`Protocol       | 约束 QueryDataTool 调用现有问数子图    |
| `app/agent/harness/tools/runtime.py`    | `ToolRuntime`Protocol         | 统一执行前校验、超时、幂等和结果归一化 |

继续复用 M1 的 `ToolResult` 和 `RunObservation`，不新增第二套结果 DTO。

### 需要新增的文件

* `app/agent/harness/tools/registry.py`
* `app/agent/harness/tools/runtime.py`
* `app/agent/harness/tools/query_data.py`
* `app/agent/harness/tools/normalizer.py`
* `app/agent/harness/tools/storage.py`
* `tests/test_harness_vertical_c.py`

### 需要修改的文件


| 文件路径                               | 函数或位置                                 | 修改内容                                                             |
| -------------------------------------- | ------------------------------------------ | -------------------------------------------------------------------- |
| `app/agent/harness/loop_controller.py` | `_execute_tool()`                          | 调用真实 ToolRuntime                                                 |
| `app/agent/harness/loop_controller.py` | `_record_observation()`                    | 将`ToolResult`映射为`RunObservation`                                 |
| `app/agent/harness/loop_controller.py` | `_continue_after_tool()`                   | 成功或部分成功后重新进入 BuildContext                                |
| `app/agent/harness/context_service.py` | `HarnessRuntimeContextProjector.project()` | 确认观察结果只复制摘要、引用和限制                                   |
| `app/agent/query_graph.py`             | `query_graph`、`build_query_graph()`       | 保持现有子图作为真实执行入口；只有发生状态或上下文不兼容时做最小改造 |
| `app/agent/nodes/execute_sql.py`       | `execute_sql()`                            | 只在真实工具接入测试暴露安全缺口时补充只读、超时或行数边界           |
| `app/agent/context_engine/engine.py`   | `ContextEngine.build()`                    | 不改变上下文编译职责                                                 |

第一版 QueryDataTool 直接复用 `app/agent/query_graph.py::query_graph`，不为了形式上的工具化立即创建 `QueryService`。只有旧图和 Harness 确实需要共享同一段业务逻辑时，才提取 `app/services/query_service.py`。

### Fake 模块清单

C 阶段不再使用 Fake Tool。允许测试中使用以下外部依赖替身，但它们不替代 ToolRuntime：


| 测试替身                      | 用途                                | 替换对象                        |
| ----------------------------- | ----------------------------------- | ------------------------------- |
| QueryGraph 的 repository fake | 隔离 Meta、DW 等外部服务            | 真实数据仓储                    |
| 测试用 Planner 或确定性 LLM   | 固定输出`tool_call -> final_answer` | 仅测试 PlanningAgent 的输入环境 |
| 测试用 ArtifactStore          | 验证引用和摘要边界                  | 生产`ResultArtifactStore`       |

每个测试替身必须说明它不是 QueryDataTool 的假实现；QueryDataTool 本身必须真实调用 `query_graph`。

### 验收测试

* 文件：`tests/test_harness_vertical_c.py`
* 测试名：`test_slice_c_start_to_result_replans_after_query_tool`
* 唯一验收标准：从 `LoopController.start()` 开始，真实 QueryDataTool 调用现有 `query_graph`，产生 `ToolResult`，转换为 `RunObservation`，重新构建 ContextEngine 上下文，再次调用 PlanningAgent 并最终返回结果；循环至少完成一次，且全链路通过。

### 本刀红线

* Tool Runtime 不得调用 PlanningAgent。
* Tool Runtime 不得控制主循环。
* QueryDataTool 不得直接修改 Harness 状态。
* 大量 rows、完整 SQL 结果和原始错误不能直接进入 `RunObservation`。
* 不得把 `query_graph` 改写成不存在的 `query_data()` 函数。
* 不在本刀接入 Analysis、Report、Knowledge Base 全部工具。
* 不在本刀实现暂停恢复。
* 不修改 M1 的状态字段和状态转换规则。

---

## 5. 切片 D：暂停与恢复

### 本刀目标

实现 `ask_user` 的持久暂停和同一 `run_id` 的恢复，确认恢复后重新构建 ContextEngine 上下文并继续执行。

### 需要先定义的 DTO / Protocol


| 文件路径                                  | 名称                            | 说明                                                |
| ----------------------------------------- | ------------------------------- | --------------------------------------------------- |
| `app/agent/harness/confirmation_store.py` | `ConfirmationStore`Protocol     | pending 确认的准备、发布、解析和幂等                |
| `app/agent/harness/confirmation_store.py` | `PauseRun`DTO                   | 运行进入`waiting_confirmation`的受控输入            |
| `app/agent/harness/confirmation_store.py` | `ResumeRun`DTO                  | 携带原始`HarnessRunRef`和`ConfirmationReply`        |
| `app/agent/harness/run_store.py`          | `HarnessRunStore`Protocol       | 运行状态、版本、active run 和恢复现场               |
| `app/agent/harness/checkpoint.py`         | `HarnessCheckpointPort`Protocol | 复用同一个`AsyncPostgresSaver`保存和读取 checkpoint |
| `app/agent/harness/loop_contracts.py`     | `RunningRunSnapshot`            | 暂停状态查询结果                                    |
| `app/agent/harness/loop_contracts.py`     | `CancelAccepted`                | 仅定义取消标记结果，本刀不扩展取消主流程            |

继续复用 M1 已冻结的：

* `ConfirmationRequest`
* `ConfirmationReply`
* `ConfirmationRecord`
* `ConfirmationStatus`
* `ConfirmationVisibility`
* `HarnessRunRef`

### 需要新增的文件

* `app/agent/harness/confirmation_store.py`
* `app/agent/harness/checkpoint.py`
* `app/agent/harness/run_store.py`
* `tests/test_harness_vertical_d.py`

### 需要修改的文件


| 文件路径                               | 函数或位置                   | 修改内容                                                |
| -------------------------------------- | ---------------------------- | ------------------------------------------------------- |
| `app/agent/harness/loop_controller.py` | `start()`                    | 处理`ask_user`，保存 pending confirmation 和 checkpoint |
| `app/agent/harness/loop_controller.py` | `resume()`                   | 校验同一 run、消费 ConfirmationReply、恢复现场          |
| `app/agent/harness/loop_controller.py` | `_pause_run()`               | 进入`waiting_confirmation/wait_confirmation`            |
| `app/agent/harness/loop_controller.py` | `_resume_run()`              | 进入`running/restore_run`，合并白名单条件               |
| `app/clients/postgres_client.py`       | `init()`、`close()`          | 向 Harness 注入已经 setup 的同一个`AsyncPostgresSaver`  |
| `app/agent/memory/types/working.py`    | `WorkingStateLoader`相关读取 | 确认恢复后从同一 thread 读取现场，不创建第二套状态源    |

### Fake 模块清单


| Fake 模块                                  | 替换时机                | 真实实现                                                 | 替换点                      |
| ------------------------------------------ | ----------------------- | -------------------------------------------------------- | --------------------------- |
| `tests/fakes/harness/fake_finalization.py` | 切片 E                  | `app/agent/harness/finalization.py::FinalizationService` | 恢复后 final\_answer 收口点 |
| 测试用外部客户端替身                       | 不替换 Harness 暂停逻辑 | PostgreSQL Checkpointer 测试环境                         | 只隔离非 Checkpointer 依赖  |

D 阶段的 Checkpointer 验收不能用纯内存状态替身代替。至少需要使用真实 `AsyncPostgresSaver` 测试环境，或者使用与生产 Saver 具有相同读写和版本语义的集成测试实现。

### 验收测试

* 文件：`tests/test_harness_vertical_d.py`
* 测试名：`test_slice_d_pause_and_resume_same_run`
* 唯一验收标准：从 `LoopController.start()` 开始触发 `ask_user` 并返回 `waiting_confirmation`；确认 Checkpointer 保留原始目标、动作序号和现场；使用相同 `run_id` 调用 `resume()` 后重新 BuildContext 并最终返回结果；全链路通过。

### 本刀红线

* `waiting_confirmation` 不能写成 completed。
* `resume()` 不能创建新的 `run_id`、`turn_id` 或 `thread_id`。
* 未验证 `confirmation_id`、身份、过期时间和字段白名单前不能恢复。
* 恢复时不能重新调用 `start_turn()`。
* 暂停期间不能调用 Planner 或 Tool Runtime。
* 暂停期间不能写最终 assistant message。
* 不触发 Memory Formation。
* 不让 API 直接修改 `pending_confirmation`。
* 不把普通进程恢复和用户确认恢复混为同一个命令。
* 不修改 M1 状态 DTO。

---

## 6. 切片 E：真实 Finalization 与 Memory Formation

### 本刀目标

将 FakeFinalization 替换为真实 FinalizationService，按确定顺序完成历史保存、最终 checkpoint、active run 释放和 Memory Formation 提交。

### 需要先定义的 DTO / Protocol


| 文件路径                                      | 名称                            | 说明                                                         |
| --------------------------------------------- | ------------------------------- | ------------------------------------------------------------ |
| `app/agent/harness/finalization.py`           | `FinalizationInput`             | 受控最终答案、运行身份、引用、限制、错误和终态意图           |
| `app/agent/harness/finalization.py`           | `FinalizationResult`            | 收口状态、历史写入结果、checkpoint 结果和 formation 提交结果 |
| `app/agent/harness/finalization.py`           | `FinalizationLedgerState`       | 记录收口阶段和版本，支持崩溃对账                             |
| `app/agent/harness/finalization.py`           | `FinalizationService`Protocol   | `finalize()`和`reconcile()`                                  |
| `app/agent/memory/contracts.py`               | `TurnMemoryInput.formation_key` | 形成任务的稳定幂等键                                         |
| `app/repositories/conversation_repository.py` | Finalization 持久化 Protocol    | 历史、输出和 active run 的条件更新                           |

`TurnMemoryInput` 已存在时只增加经过审查的 `formation_key`，不新建第二个记忆输入 DTO。

### 需要新增的文件

* `app/agent/harness/finalization.py`
* `tests/test_harness_vertical_e.py`
* `tests/test_harness_finalization_recovery.py`

### 需要修改的文件


| 文件路径                                      | 函数或位置                                          | 修改内容                                           |
| --------------------------------------------- | --------------------------------------------------- | -------------------------------------------------- |
| `app/agent/harness/loop_controller.py`        | `_finalize()`                                       | 设置 terminal intent，调用真实 FinalizationService |
| `app/repositories/conversation_repository.py` | `start_turn()`                                      | 拒绝其他非终态 active run；相同 run 请求幂等       |
| `app/repositories/conversation_repository.py` | `finish_turn()`                                     | 增加 turn/run/digest 校验和重复提交保护            |
| `app/repositories/conversation_repository.py` | 新增`release_active_run()`                          | 仅条件释放当前 run，不能清除其他 run               |
| `app/agent/turn_output.py`                    | `build_turn_output()`                               | 保持现有输出分支语义，限制最终持久化内容           |
| `app/agent/memory/contracts.py`               | `TurnMemoryInput`                                   | 增加`formation_key`                                |
| `app/agent/memory/formation_service.py`       | `submit()`                                          | 按`formation_key`查询、插入冲突对账并返回幂等结果  |
| `app/models/memory.py`                        | `MemoryFormationRunModel`                           | 增加非空唯一 key 和索引                            |
| `app/services/agent_service.py`               | `_save_turn_finish()`、`_submit_memory_formation()` | 委托 M6，删除同步/SSE 侧重复收口                   |
| `app/agent/nodes/finalize_turn.py`            | `finalize_turn()`                                   | 保留旧图兼容；Harness 路径不得重复调用             |
| `app/clients/postgres_client.py`              | Harness 依赖装配                                    | 复用同一 Checkpointer 和业务 Session               |

### Fake 模块清单

E 阶段不允许继续使用 FakeFinalization。

可以使用的测试替身：

* 测试用 Memory Repository；
* 测试用 Qdrant/Neo4j 客户端；
* 测试用事件 sink；
* 测试用时间源。

这些替身只隔离外部基础设施，不替代：

* `FinalizationService`
* `ConversationRepository`
* `MemoryFormationService`
* `Memory Governance`
* `MemoryWriter`

### Finalization 固定顺序

1. 校验 `running/finalization` 和 `terminal_intent`。
2. 生成稳定 `finalization_digest`。
3. 写入 Finalization Ledger 的 prepared 状态。
4. 保存受控 assistant message、turn output 和 execution trace。
5. 写入最终 Harness checkpoint。
6. 条件释放 `active_run_id`。
7. 使用由 digest 派生的 `formation_key` 调用 `MemoryFormationService.submit()`。
8. 保存 Formation 提交状态。
9. 返回 `FinalizationResult` 和 `LoopRunResult`。
10. 发生崩溃时由 `reconcile()` 继续，不重新调用 Planner、Tool Runtime 或 ContextEngine。

### 验收测试

* 文件：`tests/test_harness_vertical_e.py`
* 测试名：`test_slice_e_start_to_result_finalizes_history_checkpoint_and_memory`
* 唯一验收标准：从 `LoopController.start()` 开始，真实 FinalizationService 完成历史落库、终态 checkpoint、当前 `active_run_id` 条件释放和真实 `MemoryFormationService.submit()`；验证四者顺序和幂等行为后返回终态结果，全链路通过。

### 本刀红线

* Finalization 不能绕过 `MemoryFormationService`、Governance 或 Writer 直接调用 `MemoryManager.add()`。
* Formation 提交成功不等于长期记忆已经同步形成完成；自动形成返回 pending 时必须保留该语义。
* 历史保存失败不能标记 completed。
* 终态 checkpoint 未完成不能释放 active run。
* `active_run_id` 只能按当前 `run_id` 条件释放。
* 收口失败必须保持可恢复的 `running/finalization` 或独立账本状态。
* `reconcile()` 不得回到 ContextEngine、PlanningAgent 或 Tool Runtime。
* 旧固定图不能同时执行 Harness Finalization。
* 不修改 M1 冻结状态 DTO。

---

## 7. 切片依赖关系

### 必须串行

```
M1 状态层
    -> 切片 A
    -> 切片 B
    -> 切片 C
    -> 切片 D
    -> 切片 E
```
具体原因：

* B 依赖 A 的最小 Controller 入口。
* C 依赖 B 的真实 Planner、动作提交和 ToolExecutionRequest。
* D 依赖 C 已经建立的主循环和状态保存边界。
* E 依赖 D 的暂停/恢复、Checkpointer 和运行账本边界。
* 任意切片的唯一验收未通过，不进入下一刀。

### 可以并行的工作

在单人开发模式下，不并行实现不同切片。只允许以下低风险并行准备：

* 阅读旧节点和记录衔接点；
* 准备测试数据库和 Checkpointer 环境；
* 整理测试 fixture；
* 对当前真实接口做静态核对；
* 编写不改变运行行为的测试数据构造器。

不得提前实现下一切片的生产逻辑。

---

## 8. 风险提示与人工 Review 点

### 高返工风险

1. **ContextEngine 输入形状**
   * `ContextRequest.runtime_context` 已存在，但尚未经过完整 Harness 循环验证。
   * 重点检查 `ToolResult -> RunObservation -> RuntimeContext -> CompiledContext` 是否保留同一字段语义。
2. **ActionCommitter 与 Checkpointer 协调**
   * `AsyncPostgresSaver` 与业务 PostgreSQL 当前没有可直接确认的共享事务接口。
   * 必须坚持 prepared、checkpoint、committed 和恢复对账，不得使用“原子事务”表述掩盖实际边界。
3. **QueryDataTool 与现有 query\_graph 的状态兼容**
   * `query_graph` 使用 `AgentState`，而 Harness 使用 `HarnessGraphState`。
   * 需要人工检查 LangGraph 状态合并、运行上下文注入和结果字段是否兼容。
4. **暂停恢复的身份一致性**
   * 当前 `thread_id == conversation_id`。
   * `resume()` 不能创建新 thread，也不能通过新的 turn 初始化覆盖旧现场。
5. **Finalization 的重复写入**
   * 当前 `finish_turn()` 会创建新的 assistant message 和 output。
   * 必须人工 Review 相同 digest 重试、进程崩溃和未知提交结果。
6. **Memory Formation 幂等**
   * 当前没有 `formation_key`。
   * 需要数据库唯一约束和并发冲突后的重新读取，不能只在 Python 代码中查重。
7. **旧图与 Harness 双重收口**
   * `AgentService`、`finalize_turn` 和 Harness 可能重复保存历史。
   * E 阶段必须检查同步入口、SSE 入口和旧图入口是否只保留一个收口责任。

### 必须人工 Review

* M1 冻结 DTO 是否真的没有被后续切片偷偷修改。
* `action_seq` 是否只在动作提交成功后递增。
* 同一 `action_id` 重试是否没有重复执行非幂等工具。
* `waiting_confirmation` 是否始终不写最终历史。
* 终态 checkpoint、Finalization Ledger、conversation turn 和 Memory Formation 的顺序。
* 所有 Fake 模块的 docstring 是否包含真实替换路径和替换点。
* 每个切片是否只有一个从 `LoopController.start()` 到最终返回结果的验收测试。
* 所有大对象是否只通过 Artifact 引用进入状态、上下文和历史。
* 当前未实现用户级权限校验的事实是否被保留，不能误写成已完成能力。

---

## 9. 完成 E 后的开发准入条件

只有以下条件全部满足，才可以进入 API、SSE 和旧图迁移：

* A～E 五个纵向切片按顺序完成。
* 每个切片的唯一端到端验收测试全绿。
* `HarnessControlState`、`HarnessRunRef`、`HarnessStateSnapshot` 未被偷改。
* ContextEngine 只保留现有 `CompiledContext` 作为输出。
* QueryDataTool 已通过现有 `query_graph` 完成真实循环。
* 暂停和恢复保持同一 `run_id`、`turn_id`、`thread_id`。
* Finalization 已完成历史、checkpoint、active run 和 Formation 的顺序验证。
* 旧 `AgentService` 路径不会与 Harness 重复执行收口。
* 最后再单独处理 API/SSE 迁移、生产依赖装配和旧图兼容回归。
