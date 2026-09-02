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

## M1 独立 Memory 模块

- [x] 在 `app/agent/memory/` 建立独立领域模块，不自动接入 `agent_graph`。
- [x] Working Memory 只读 `AgentState.messages + AsyncPostgresSaver`。
- [x] Episodic Memory 保存具体任务、过程、结果和经验。
- [x] Semantic Memory 保存稳定事实、偏好、规则和显式实体关系。
- [x] Perceptual Memory 当前启用文本，预留 image/audio/video 编码器与 collection。
- [x] PostgreSQL 保存记忆正文、结构化数据、来源、版本、附件和索引任务。
- [x] Qdrant 支持用户、会话、项目和模态过滤。
- [x] Neo4j 支持用户隔离的实体、关系和来源记忆投影。
- [x] 支持遗忘、过期、版本替换、访问统计和失败索引重试。
- [x] 基础设施暂未启用时保留待重建状态，不丢失后续投影入口。
- [x] 提供 `MemoryManager` 与 `build_memory_runtime()` 统一入口。
- [x] Neo4j、Qdrant、Embedding 不可用时不破坏 PostgreSQL 事实记录。

## M2 真实基础设施验收

- [x] 启动 PostgreSQL、Qdrant、Embedding 和 Neo4j。
- [ ] 在空数据库创建 Memory 业务表和 LangGraph Checkpointer 官方表。
- [x] 在临时空数据库幂等创建 5 张 Memory 业务表、表注释和索引。
- [x] 确认现有 `agent_app` 中 4 张 LangGraph Checkpointer 官方表可用。
- [x] 验证 Episodic/Semantic/Perceptual 文本写入、检索和作用域隔离。
- [x] 验证 Qdrant collection、payload 索引与 1024 维向量。
- [x] 验证 Neo4j 约束、索引、APOC、实体关系投影、一跳召回和删除。
- [x] 验证先写 PostgreSQL，后恢复 Qdrant/Neo4j 的索引重建。
- [ ] 增加 Memory 管理命令或受保护的管理 API。

## M3 记忆提取与治理

- [ ] 定义可审计的记忆候选结构和提取提示词版本。
- [ ] 显式“记住”同步写入，其余长期记忆候选异步提取。
- [ ] 依据事实、偏好、任务经验和附件来源选择记忆类型。
- [ ] 在写入前执行权限校验、去重、置信度判断和敏感信息过滤。
- [ ] 对可更新事实使用版本替换，对不同条件下同时成立的事实保留条件。
- [ ] 建立长期记忆查看、修正、遗忘和来源追溯接口。
- [ ] 建立提取准确率、误记率、召回率和用户纠正率评测集。

## C1 ContextEngine

- [ ] `gather()` 收集 Working、Episodic、Semantic、Perceptual 和业务 RAG 候选。
- [ ] `select()` 按权限、相关性、重要性、时间和 token 预算选择。
- [ ] `resolve()` 解析“刚才、上次、这张图”等会话与附件引用。
- [ ] `deduplicate()` 合并重复事实，并处理版本、条件和冲突。
- [ ] `summarize()` 生成可持久化的较早会话摘要，保留来源范围。
- [ ] `compress()` 在本轮上下文超预算时压缩工具结果或候选内容。
- [ ] `compile()` 输出稳定的 system/messages/evidence/attachments 结构。
- [ ] `trace()` 记录本轮召回、过滤、使用和丢弃了哪些上下文。
- [ ] 策略按 Agent 类型注入，Memory 存储实现保持不变。

## C2 Agent 接入

- [ ] 先接入独立日常聊天回归链路，验证多轮引用和跨会话长期记忆。
- [ ] 再接入简单问数，历史只传问题、结果摘要和结果引用，不传完整 rows。
- [ ] 接入分析报告，引用 `turn_output`、任务证据和报告摘要。
- [ ] 区分会话历史展示、Checkpointer 图状态、长期记忆和最终模型上下文。
- [ ] 保持现有 SSE、会话历史接口和报告渲染协议不变。

## D1 决策支持闭环

- [ ] 增加 `decision_support` 路由和独立决策计划。
- [ ] 复用问数、分析报告和 ContextEngine，不复制 SQL/报告节点。
- [ ] 输出关键发现、原因假设、证据、建议动作、优先级、风险和限制。
- [ ] 原因与建议必须引用当前或历史证据；无证据内容明确标为假设。
- [ ] 保存可重新渲染的 decision report 和决策依据 trace。
- [ ] 支持用户追问、修正假设、比较方案和继续执行分析。
- [ ] 建立决策报告事实一致性和行动可执行性评测。

## 当前验收边界

M1 完成的是可独立调用的 Memory 基础设施和领域能力。它尚未决定每一轮要读取或写入哪些记忆，也尚未把记忆编译进模型上下文；这些职责分别属于 M3、C1 和 C2。
