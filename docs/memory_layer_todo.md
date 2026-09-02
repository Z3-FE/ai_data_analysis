# Data Agent 记忆层与上下文工程 TODO

> 目标：参考 Hello-Agents 第 8、9 章，建立可持续运行的四类记忆、上下文编译和
> Decision Agent 闭环。每个里程碑都在现有能力上累加，不用临时规则替代最终边界。

## 进度总览

| 里程碑 | 目标 | 状态 | 依赖 |
| --- | --- | --- | --- |
| M0 | 契约和架构边界 | 已完成 | 现有 ContextEngine |
| M1 | PostgreSQL 记忆事实存储 | 代码完成，联调待验收 | M0 |
| M2 | Working Memory 和会话活动视图 | 待开发 | M1 |
| M3 | Episodic 和 Semantic 长期记忆 | 待开发 | M1、M2 |
| M4 | Qdrant 语义检索索引 | 待开发 | M3 |
| M5 | Perceptual Memory 和资源引用 | 待开发 | M1、M2 |
| M6 | 记忆提取与整合 | 待开发 | M2、M3、M5 |
| M7 | 接入 ContextEngine | 待开发 | M2 至 M6 |
| M8 | 接入 Data Agent 主链路 | 待开发 | M7 |
| M9 | Decision Agent 闭环 | 待开发 | M8 |
| M10 | 治理、评估和长期运行 | 待开发 | M1 至 M9 |

## M0：契约和架构边界

- [x] 确定记忆层目录为 `app/agent/memory/`。
- [x] 保留 `app/context_engine/` 作为通用上下文编译内核。
- [x] 定义 Working、Episodic、Semantic、Perceptual 四类记忆。
- [x] 定义 MemoryItem、MemoryScope、MemorySource、MemoryStatus 和读取请求。
- [x] 定义与 PostgreSQL、Qdrant 无关的异步 MemoryProvider 契约。
- [x] 明确原始历史、记忆、Runtime Evidence、RAG 和 Checkpointer 的边界。
- [x] 明确摘要、上下文压缩和记忆整合的区别。
- [x] 为记忆模型契约补充单元测试；Provider 实现测试在 M1 随存储实现增加。

### 验收

- [x] 业务节点不需要导入数据库客户端即可描述记忆。
- [x] 默认 importance 和 confidence 均为中性值 0.5，不按角色固定赋值。
- [x] MemoryItem 不直接依赖 ContextItem。

## M1：PostgreSQL 记忆事实存储

- [x] 设计 `agent_memories` 统一事实表。
- [x] 增加用户、租户、Agent、项目和会话作用域字段及索引。
- [x] 增加来源、状态、版本、有效期、重要性和置信度字段。
- [x] 使用 JSONB 保存各记忆类型的结构化扩展数据。
- [x] 增加 SQLAlchemy 模型和带 COMMENT 的 PostgreSQL 建表脚本。
- [x] 实现 PostgreSQLMemoryProvider 的 add/search/update/archive。
- [x] 增加作用域隔离、软归档和基础边界验证。
- [x] 明确记忆表不由 LangGraph `AsyncPostgresSaver.setup()` 管理。
- [ ] 增加真实 PostgreSQL 联调和并发更新验证。

### 验收

- [x] PostgreSQL 是记忆事实唯一可信来源。
- [x] 不同 user_id、tenant_id 和 agent_id 之间不能串用记忆。
- [x] 一条记忆可以追溯到原始轮次、消息或资源。
- [ ] 在目标 PostgreSQL 实例执行脚本并完成真实读写验收。

## M2：Working Memory 和会话活动视图

- [ ] 实现 Working Memory 精确读取和更新服务。
- [ ] 从原始会话消息构建当前活动目标、有效约束和未完成任务。
- [ ] 保存活动 asset_id 和结果引用，不复制大型结果。
- [ ] 设计可更新的 conversation snapshot，不用固定“最近 N 轮”作为唯一边界。
- [ ] 会话增长时生成摘要，同时保留原始消息和来源引用。
- [ ] 区分摘要触发、上下文预算压缩和长期记忆整合。
- [ ] 增加同一会话连续追问和重启恢复验证。

### 验收

- [ ] “那 11 月呢”“继续刚才的分析”能找到当前活动对象。
- [ ] Working 不无限复制完整消息、rows、SQL 和 SSE。
- [ ] 摘要后仍能按引用读取更早原始历史。

## M3：Episodic 和 Semantic 长期记忆

- [ ] 定义 Data Agent 可沉淀的情景记忆结构。
- [ ] 保存已完成任务、结果摘要、处理策略、失败原因和修正结果。
- [ ] 定义稳定事实、业务约定、用户偏好和项目配置的语义记忆结构。
- [ ] 支持按结构化作用域精确查询。
- [ ] 自动提取内容只作为候选，不直接覆盖已确认事实。
- [ ] 增加来源、置信度、有效期、冲突和 superseded 关系。
- [ ] 为用户提供长期记忆的查看、修改和删除边界。

### 验收

- [ ] 可回忆相似的历史分析经历。
- [ ] 可稳定使用已确认的指标定义、项目约定和用户偏好。
- [ ] 新旧事实冲突时保留来源，不静默覆盖。

## M4：Qdrant 语义检索索引

- [ ] 为 Episodic、Semantic 和 Perceptual 文本观察定义 collection 与 payload。
- [ ] PostgreSQL 写入成功后同步或异步更新向量索引。
- [ ] 检索前执行作用域过滤，检索后返回 PostgreSQL memory_id。
- [ ] 支持索引重建、模型版本升级和删除同步。
- [ ] 增加相关性阈值、top_k 和召回质量评测。
- [ ] Working Memory 默认不依赖向量检索。

### 验收

- [ ] 删除 Qdrant collection 后可以从 PostgreSQL 完整重建。
- [ ] 向量检索不能绕过用户、租户、Agent 和项目作用域。

## M5：Perceptual Memory 和资源引用

- [ ] 设计 assets、message_asset_refs 和资源观察数据结构。
- [ ] 保存文件类型、哈希、位置、权限、来源消息和生命周期。
- [ ] 图片、音频、PDF 原始内容进入文件或对象存储。
- [ ] OCR、音频转写、版面和视觉观察进入 Perceptual Memory。
- [ ] Working Memory 保存当前活动资源引用。
- [ ] 实现“这张图”“第二个文件”等跨轮资源引用解析。
- [ ] 对颜色、位置和布局问题重新加载原始资源。
- [ ] 临时上传资源不自动写入 RAG 知识库。

### 验收

- [ ] 第二轮可以继续询问第一轮上传的图片或文件。
- [ ] 资源权限、删除和过期状态会阻止后续读取。
- [ ] 派生观察可以追溯到原始 asset_id 和解析版本。

## M6：记忆提取与整合

- [ ] 定义 MemoryCandidate 和提取结果结构。
- [ ] 一轮结束后，从用户输入、最终回答、报告摘要和真实证据提取候选。
- [ ] 用户显式“记住”进入高优先级候选，但拒绝敏感凭证。
- [ ] 区分确认事实、用户偏好、模型推断和运行经验。
- [ ] 实现 ADD、UPDATE、MERGE、ARCHIVE、IGNORE、CONFLICT。
- [ ] 使用确定性规则处理精确重复和明确过期。
- [ ] 仅在语义重复、冲突判断等必要场景调用 LLM。
- [ ] 增加后台整合任务，不阻塞用户首屏回答。

### 验收

- [ ] 普通寒暄不会产生无意义长期记忆。
- [ ] 模型推断不会被自动保存为确认事实。
- [ ] 重复事实不会无限增长，冲突事实不会静默覆盖。

## M7：接入 ContextEngine

- [ ] 实现 MemoryItem -> ContextItem 适配层。
- [ ] 固定 Active Conversation、Referenced History、Relevant Knowledge、
  Referenced Resources 等上下文分区。
- [ ] Runtime Evidence 和 RAG Evidence 使用独立来源，不伪装成长期记忆。
- [ ] 建立节点级 ContextPolicy 和 Token 预算策略。
- [ ] 实现作用域过滤、相关性选择、去重、冲突处理和来源保留。
- [ ] 优先使用已有摘要；必要时生成新摘要；最后才确定性截断。
- [ ] 持久化 ContextTrace，记录选中、丢弃、压缩和来源引用。

### 验收

- [ ] 能解释某轮模型使用了哪些历史、记忆、资源和证据。
- [ ] 检索过但未命中的记忆不会进入模型上下文。
- [ ] 超预算只改变本轮上下文视图，不删除原始历史和记忆。

## M8：接入 Data Agent 主链路

- [ ] 在路由前编译最小会话上下文并生成 resolved_question。
- [ ] 元数据召回、SQL、Python、报告节点分别使用最小上下文。
- [ ] 节点完成后的真实结果进入 Runtime Evidence。
- [ ] 待执行计划不进入 Runtime Evidence。
- [ ] 保存 context_refs、asset_refs 和 evidence_refs。
- [ ] 支持日常聊天、简单问数、数据分析任意顺序切换。
- [ ] 保持现有报告和执行过程前端协议稳定。

### 验收

- [ ] 跨轮省略表达可以可靠补全。
- [ ] 更早历史可通过摘要引用或相关性检索按需取回。
- [ ] 完整 rows、全部 SSE 和无关节点状态不会被传给所有 LLM。

## M9：Decision Agent 闭环

- [ ] 增加 decision_support 路由和结果结构。
- [ ] 输入仅包含当前问题、RenderedReport 摘要、关键发现、任务摘要、证据和限制。
- [ ] 生成发现、原因假设、建议动作、优先级、风险和待验证事项。
- [ ] 区分证据支持结论和待验证推断。
- [ ] 保存 decision_report 和证据引用。
- [ ] 保存用户对建议的采纳、拒绝、修改和执行反馈。
- [ ] 将可复用的决策经历提取为 Episodic 候选。
- [ ] 将确认后的业务约定提取为 Semantic 候选。

### 验收

- [ ] 用户可以围绕之前报告进行连续决策追问。
- [ ] 每条建议可以追溯到分析证据或明确标记为假设。
- [ ] 用户反馈可以影响后续相似决策，但不会直接覆盖事实。

## M10：治理、评估和长期运行

- [ ] 长期记忆查看、编辑、删除、导出和审计接口。
- [ ] 保存期限、自动归档、资源过期和删除传播策略。
- [ ] 敏感信息识别、权限校验和跨租户隔离验证。
- [ ] 记忆提取准确率、检索召回率和上下文使用率评测集。
- [ ] 冲突率、重复率、索引延迟、Token 和成本监控。
- [ ] 摘要失真、错误记忆污染和错误引用的回归测试。
- [ ] 评估是否需要 Neo4j 支持多跳实体关系和数据血缘。
- [ ] 形成企业知识库 Agent、智能助手等新 Agent 的策略接入模板。

### 验收

- [ ] 记忆可解释、可追溯、可修改、可删除。
- [ ] 长会话和长期运行不会导致上下文无限增长。
- [ ] 新 Agent 可复用通用 ContextEngine 和记忆契约，并提供独立策略。

## 当前执行位置

M1 实现备注：agent_memories 的 ORM、Provider、MemoryManager 和带 COMMENT 的 PostgreSQL
建表脚本已经完成；真实 PostgreSQL 联调及并发验证仍待执行。

~~~text
当前里程碑：M1 PostgreSQL 记忆事实存储（代码已完成，真实数据库联调待验收）
已完成：目录边界、四类记忆模型、Provider 契约、PostgreSQL ORM、建表脚本、CRUD 门面
下一步：完成 PostgreSQL 实例联调后进入 M2 Working Memory 和会话活动视图
~~~
