# Data Agent 记忆、上下文与决策闭环 TODO

本文档把记忆层、上下文工程和决策支持拆成可累加的模块。每个阶段只依赖前一阶段的稳定接口，避免后续能力增强时推翻已经完成的存储和领域边界。

## 固定边界

```text
AgentState + LangGraph Checkpointer
    -> 当前线程 Working Memory

PostgreSQL
    -> Episodic / Semantic / Perceptual 的事实来源

Qdrant
    -> 三类长期记忆的可重建向量投影

Neo4j
    -> Semantic Memory 的可重建实体关系投影

ContextEngine
    -> 选择、压缩和编译上下文，不拥有记忆事实

Agent / Decision Agent
    -> 消费 ContextEngine 输出，不直接拼接数据库记录
```

## Memory 与 ContextEngine 的边界

`ContextEngine` 只依赖 `MemoryContextReader`，也就是 `MemoryManager` 的以下只读能力：

- `load_working()`：从当前会话的 Checkpointer 状态读取短期对话。
- `search()`：按一种记忆类型检索；类型内部负责 PostgreSQL、Qdrant、Neo4j 的混合召回和专属排序。
- `search_many()`：当策略明确需要多种记忆时并行检索并合并结果，调用方传入类型组合，不由 Memory 强制四类全查。
- `get_asset()`：解析历史附件引用；返回的是附件元数据和已提取文本，不等同于把原文件直接塞进模型上下文。
- `get_sources()`：为被选中的记忆读取真实来源，供上下文引用和 trace 使用。

`ContextEngine` 不直接调用 Formation、Governance、Writer、Lifecycle、PostgreSQL Repository、Qdrant 或 Neo4j。长期记忆提取、去重、版本替换、过期清理和投影同步属于 Memory 内部职责。

下列扩展点保留，因为未来 ContextEngine 或 Memory 实现确实会使用：

- `MemoryRecord.structured_data`：承载事实条件、事件键、附件 ID 和已确认的实体关系。
- `MemoryAsset.modality/metadata`：为后续 image、audio、video 记忆保留统一附件身份，但当前只形成 text 记忆。
- `MemoryEncoder.encode()`：为 Qdrant 投影提供可替换的文本/多模态编码边界。当前不保留没有调用方的 `encode_many()` 或通用编码器注册表。
- Qdrant 的按类型、作用域和模态 collection，以及 Semantic 的 Neo4j 关系投影：ContextEngine 不感知具体数据库，只通过 `search()` 获得统一结果。
- `MemorySource` 和 `memory_graph_projections`：为来源追溯和投影重建保留事实输入。
- `MemoryCreate` 只承载治理后的候选内容；`memory_id`、`version` 和 `supersedes_memory_id` 由 PostgreSQL 原子写入内部生成，不向 ContextEngine 或上层开放。

以下内容不属于 ContextEngine，不能为了“未来可能需要”塞进其输入：`memory_formation_runs`、`memory_index_jobs`、`MemoryLifecycle`、候选治理字段和投影失败记录。它们分别服务于记忆形成审计、投影故障记录和后台维护。当前投影失败只记录，不启动重试消费者。

## M1 独立 Memory 模块

- [x] 在 `app/agent/memory/` 建立独立领域模块，不自动接入 `agent_graph`。
- [x] Working Memory 只读 `AgentState.messages + AsyncPostgresSaver`。
- [x] Episodic Memory 保存具体任务、过程、结果和经验。
- [x] Semantic Memory 保存稳定事实、偏好、规则和显式实体关系。
- [x] Perceptual Memory 当前启用文本，预留 image/audio/video 编码器与 collection。
- [x] PostgreSQL 保存记忆正文、结构化数据、来源、版本、附件和索引任务。
- [x] Qdrant 支持用户、会话、项目和模态过滤。
- [x] Neo4j 支持用户隔离的实体、关系和来源记忆投影。
- [x] 支持遗忘、过期、版本替换和访问统计。
- [x] 真实的 Qdrant/Neo4j 投影失败会记录到 `memory_index_jobs`，当前状态写入 `failed`，不启动消费者或自动重试。
- [x] 提供 `MemoryManager` 与 `build_memory_runtime()` 统一入口。
- [x] Neo4j、Qdrant、Embedding 不可用时不破坏 PostgreSQL 事实记录。

## M2 真实基础设施验收

- [x] 启动 PostgreSQL、Qdrant、Embedding 和 Neo4j。
- [x] 保留现有 LangGraph Checkpointer 官方表，只验证其可用性，不重复创建。
- [x] 在临时空数据库幂等创建 6 张 Memory 业务表、表注释和索引。
- [x] 确认现有 `agent_app` 中 4 张 LangGraph Checkpointer 官方表可用。
- [x] 验证 Episodic/Semantic/Perceptual 文本写入、检索和作用域隔离。
- [x] 验证 Qdrant collection、payload 索引与 1024 维向量。
- [x] 验证 Neo4j 原生约束、索引、实体关系投影、一跳召回和删除；当前不依赖 APOC、GDS 或 neo4j-graphrag。
- [x] 验证 PostgreSQL 事实写入与 Qdrant/Neo4j 投影同步边界。
- [ ] 增加 Memory 管理命令或受保护的管理 API。

## M3 记忆提取与治理

- [x] 定义可审计的记忆候选结构和提取提示词版本。
- [x] 显式“记住”同步写入，其余长期记忆候选异步提取。
- [x] 依据事实、偏好、任务经验和附件来源选择记忆类型。
- [x] 在写入前执行权限校验、去重、置信度判断和敏感信息过滤。
- [x] 对可更新事实使用版本替换，对不同条件下同时成立的事实保留条件。
- [ ] 建立长期记忆查看、修正、遗忘和来源追溯接口。
- [ ] 建立提取准确率、误记率、召回率和用户纠正率评测集。

## C1 ContextEngine

- [x] `gather()` 收集 Working、Episodic、Semantic、Perceptual 和可选业务 RAG 候选。
- [x] `select()` 按相关性、重要性、可信度、时间、策略优先级和 token 预算选择。
- [x] `resolve()` 使用稳定 `asset_id` 解析当前附件、历史附件、文件名和序号引用。
- [x] `deduplicate()` 按正文、类型化身份、条件和版本合并重复候选。
- [x] `summarize()` 按消息覆盖范围分批生成可持久化的增量会话摘要。
- [x] `compress()` 在单次构建超预算时按真实 token 边界压缩候选，不反写记忆。
- [x] `compile()` 输出标准 role/content 消息和稳定语义分区。
- [x] `trace()` 保存问题哈希、来源 ID、分数、token 和处理原因，不复制候选正文。
- [x] 策略、Planner、Summarizer、Compressor、RAG 和 Store 均可注入替换，Memory 存储实现保持不变。
- [x] PostgreSQL 保存 `context_conversation_summaries` 和 `context_build_runs`，与 LangGraph Checkpointer 官方表分离。
- [x] `finalize_turn` 将本轮附件 ID 写入 Working 消息元数据，支持后续历史附件引用。

## 当前验收边界

M3 已完成本轮结束后的长期记忆形成闭环：历史成功落库后执行 Eligibility、候选提取、治理、去重、版本替换、MemoryManager 写入和审计。显式“记住”同步形成，自动候选和文本附件异步形成；失败不会覆盖已经生成的回答。

当前长期记忆的形成已经接入轮次结束流程，但还不会在下一轮自动读取这些长期记忆，也不会改变 Agent 图的模型上下文。`MemoryContextReader`、四类记忆的召回和附件读取已经作为 C1 的稳定只读入口保留；长期记忆的选择、引用解析、摘要、压缩和上下文编译仍属于 C1，后续统一由 Data Agent Harness 决定如何接入各类 Agent。

C1 已在 `app/agent/context_engine/` 独立完成：输入 `ContextRequest`，输出 `CompiledContext(messages/sections/trace)`。默认工厂使用真实 `tiktoken` 预算、LLM 结构化 Planner 与摘要器，并保留确定性回退；长期记忆仍只通过 `MemoryContextReader` 读取。当前没有在应用启动时创建 ContextEngine，也没有把输出接入 `agent_graph`，因此现有日常聊天、问数、分析、SSE 和报告协议尚未改变；后续由 Data Agent Harness 统一决定接入方式。

`memory_index_jobs` 当前只保存已启用投影的真实失败，状态为 `failed`；后续如果决定做后台修复，再增加消费者和状态流转。在此之前不把失败记录描述成已经具备自动重试。固定 `dev-user` 仍是登录/authentication 完成前的调试边界，不能作为多用户实现。
