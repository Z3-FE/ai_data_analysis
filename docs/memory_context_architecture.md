# Data Agent 记忆层与上下文工程架构

## 一、建设目标

本方案在现有 Data Agent 上增加可长期演进的记忆层，并继续复用通用
app/context_engine/。目标不是让每个问题机械地经过四类记忆，而是建立统一的
候选收集、按需检索、上下文编译、记忆提取和生命周期治理流程。

~~~text
app/agent/memory/
    Data Agent 专用记忆层，认识会话、分析任务、报告和资源

app/context_engine/
    通用上下文编译内核，不认识具体 Agent 和数据库
~~~

未来企业知识库 Agent、智能助手等项目可以复用 ContextEngine 的编译能力；它们应有
各自的记忆适配层和策略，不能直接共享 Data Agent 的业务实现。

## 二、总体流程

~~~mermaid
flowchart TD
    A[用户文本和附件] --> B[身份与作用域校验]
    B --> C[Working Memory 精确读取]
    C --> D[识别活动引用和检索需求]
    D --> E1[Episodic 检索]
    D --> E2[Semantic 检索]
    D --> E3[Perceptual 精确读取或检索]
    D --> E4[RAG 外部知识检索]
    C --> F[候选上下文]
    E1 --> F
    E2 --> F
    E3 --> F
    E4 --> F
    F --> G[ContextEngine 隔离、选择、压缩和组装]
    G --> H[路由 / 问数 / 分析 / 报告 / 决策]
    H --> I[Runtime Evidence]
    I --> J[生成本轮回答]
    J --> K[保存原始历史和可渲染输出]
    K --> L[Memory Extraction]
    L --> M[Memory Consolidation]
    M --> N[更新四类记忆和检索索引]
~~~

## 三、四类记忆如何调用

四类记忆是不同的数据语义和生命周期，不代表四个模型调用，也不要求每轮把四类结果
全部加入上下文。

### 1. Working Memory

每轮都按 conversation_id 精确读取，但只返回当前活动视图：

~~~text
当前会话目标
最近仍有效的约束
正在进行或等待继续的任务
最近资源引用 asset_id
历史摘要的引用
~~~

Working 不保存无限增长的完整消息。原始消息仍在 conversation_messages；当活动上下文
增长时，Working 保存可更新的会话摘要和仍需保留的原始消息引用。

### 2. Episodic Memory

保存已经结束的经历，例如某次分析采用了什么步骤、得到什么结果、哪里失败、如何修正。
根据当前问题进行语义检索和结构化过滤，只有超过相关性阈值的经历才成为上下文候选。

适合的问题包括：

~~~text
继续上次的分析
以前怎么处理过类似问题
复用之前成功的查询或分析方案
避免重复某次失败
~~~

### 3. Semantic Memory

保存相对稳定的事实、约定、偏好和业务定义。精确作用域事实可直接读取，数量较大的事实
集合通过语义检索获得候选。未经确认的模型推测不得写成 Semantic 事实。

### 4. Perceptual Memory

保存多模态资源的身份与派生观察：

~~~text
asset_id
原始文件位置和权限
OCR 或音频转写
图片、页面和表格的观察结果
观察使用的模型和版本
来源消息与会话
~~~

本轮上传的附件直接创建活动资源引用。用户后续说“刚才那张图”时，先从 Working 找到
asset_id，再读取 Perceptual Memory。若问题涉及颜色、位置、图形关系或版面，应通过
asset_id 重新加载原始资源，而不是只依赖文字摘要。临时附件默认不进入 RAG 知识库。

### 5. 是否检索的判定

第一版不使用写死的中文关键词决定全部行为，也不为每类记忆单独调用一次 LLM：

1. Working 始终精确读取。
2. 当前请求带附件或活动资源引用时读取 Perceptual。
3. Semantic 和 Episodic 可以并行做轻量检索，但检索结果只是候选，未命中不进入上下文。
4. 路由、节点策略和结构化引用可以提高或禁止某类记忆的检索优先级。
5. 引用对象存在歧义且无法可靠解析时请求用户澄清，不猜测。

因此“检索过”不等于“模型看到了”。最终是否加入模型输入由权限、作用域、相关性、
冲突状态和 Token 预算共同决定。

## 四、模型上下文结构

上下文模板结构固定，不根据记忆类型切换；没有内容的区块省略。

~~~text
[Context Metadata]
request_id、user_id、conversation_id、agent_id、node_id、时间和预算

[Role & Policies]
系统身份、权限、数据边界和节点职责

[Current Request]
用户本轮原始输入、已解析引用和明确约束

[Active Conversation]
当前目标、仍有效约束、活动任务和必要的近期对话

[Referenced History]
本轮真正引用的历史任务、结果摘要、成功或失败经验

[Relevant Knowledge]
与本轮相关的稳定事实、业务定义、项目约定和用户偏好

[Referenced Resources]
asset_id、派生观察、来源；必要时附真实多模态输入

[Runtime Evidence]
本轮已经执行完成的 SQL、分析任务和报告证据摘要

[RAG Evidence]
外部知识库检索结果及来源

[Context Handling]
冲突、缺失、压缩、丢弃和不确定性说明

[Output Contract]
当前节点必须返回的结构、字段和引用要求
~~~

| 上下文区块 | 主要来源 |
| --- | --- |
| Active Conversation | Working Memory |
| Referenced History | Episodic Memory |
| Relevant Knowledge | Semantic Memory |
| Referenced Resources | Perceptual Memory |
| Runtime Evidence | 当前运行已完成结果，不属于长期记忆 |
| RAG Evidence | 外部知识库，不属于用户记忆 |

规划阶段只能在 Current Request 或独立执行计划中描述待执行任务，不能提前生成不存在的
Runtime Evidence。

## 五、运行边界

### 运行前

读取原始请求、已有 Working、相关长期记忆和资源引用，然后编译路由或当前节点上下文。
此时不能知道本轮尚未查询出的商品类别、销售额、原因或决策结论。

### 运行中

每个节点只接收完成职责所需的最小上下文。SQL、Python、报告和决策节点分别编译，
不能把完整 AgentState 原样发给所有模型。节点完成后的真实结果进入 Runtime Evidence，
并携带 turn_id、task_id 和来源引用。

### 运行后

先保存原始历史和用户可见输出，再提取记忆候选。提取器只生成候选，不直接覆盖长期
事实；整合器负责决定：

~~~text
ADD       新增
UPDATE    更新同一事实
MERGE     合并重复内容
ARCHIVE   归档失效内容
IGNORE    不值得长期保存
CONFLICT  与现有事实冲突，保留待确认
~~~

用户显式要求“记住”的非敏感信息优先进入候选，但仍要记录来源、作用域和可删除边界。

## 六、摘要、压缩和整合

三者分开实现：

| 能力 | 触发时机 | 处理对象 | 是否修改原始历史 |
| --- | --- | --- | --- |
| Summary | 一轮结束、阶段结束或活动上下文过长 | 一段会话或任务内容 | 否 |
| Compaction | 本轮编译上下文预计超过预算 | 本轮候选上下文视图 | 否 |
| Consolidation | 新长期记忆候选产生后或后台维护时 | 长期记忆事实和版本 | 否，旧版本归档或标记 |

压缩顺序建议：去除重复 -> 引用大型结果 -> 丢弃低相关项 -> 使用已有摘要 -> 必要时生成
新摘要 -> 仍超限时确定性截断。所有操作进入 ContextTrace，便于回答“本轮用了什么”。

## 七、存储设计

PostgreSQL 是记忆事实来源。初始采用统一 agent_memories 表，通过 memory_type、作用域、
状态和 JSONB 承载四类公共数据；资源身份和消息资源关系使用独立表。只有当某类记忆的
访问模式或字段差异被真实验证后，才拆专用表。

Qdrant 只保存可重建的向量索引和检索元数据，优先索引 Episodic、Semantic 和
Perceptual 文本观察。Working 以 PostgreSQL 精确读取为主。删除、归档或更新 PostgreSQL
事实后，异步同步 Qdrant 索引。

图片、音频、PDF 等原始内容由文件或对象存储保存。PostgreSQL 保存 asset_id、路径、
权限、哈希、类型和生命周期；Perceptual Memory 保存派生观察。

Neo4j 当前不作为前置依赖。未来业务确实需要实体关系、多跳追踪、影响链路或复杂血缘
查询时，可以把 Semantic 中部分事实同步到 Neo4j；不能为了复刻教程而先引入。

## 八、Decision Agent 闭环

~~~mermaid
flowchart LR
    A[用户问题] --> B[记忆读取与 ContextEngine]
    B --> C[Data Agent 查询和分析]
    C --> D[RenderedReport 和分析证据]
    D --> E[Decision Agent]
    E --> F[发现、原因假设、动作、优先级、风险和待验证项]
    F --> G[用户反馈或后续追问]
    G --> H[保存历史并提取记忆候选]
    H --> I[整合 Working / Episodic / Semantic / Perceptual]
    I --> B
~~~

Decision Agent 默认只接收当前问题、RenderedReport 摘要、关键发现、任务摘要、证据引用
和限制，不接收完整 rows、完整 AgentState、全部 SSE 或无关 SQL。数据支持的结论与需要
进一步验证的推断必须分开。

## 九、稳定边界

1. 原始历史是审计事实，摘要和压缩不会删除它。
2. Checkpointer 管图恢复，不替代会话历史和长期记忆。
3. 记忆层管“保存和检索什么”，ContextEngine 管“本轮给模型什么”。
4. PostgreSQL 是事实来源，向量索引可以重建。
5. 记忆必须带用户和业务作用域，跨作用域默认不可见。
6. 自动提取的事实带置信度、来源和生命周期；不确定内容不能覆盖确认事实。
7. 每个 Agent 和节点可以有不同策略，但复用同一套模型和 Provider 契约。
