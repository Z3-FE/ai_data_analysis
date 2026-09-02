# Data Agent Memory

`app/agent/memory/` 是 Data Agent 的记忆层。它参考 Hello-Agents 第 8 章的
Working、Episodic、Semantic、Perceptual 四类记忆，但不复制教程的内存实现，
而是按当前项目的 PostgreSQL、Qdrant 和资源存储边界实现。

## 目录职责

~~~text
app/agent/memory/
├── models.py          四类记忆的稳定数据契约
├── interfaces.py      与基础设施无关的 Provider 接口
├── postgres.py        PostgreSQL 事实模型和第一版 Provider
├── manager.py         四类记忆的类型化调用门面
├── working.py         当前会话活动状态和资源引用（后续）
├── episodic.py        已完成任务和可复用经历（后续）
├── semantic.py        稳定事实、约定和偏好（后续）
├── perceptual.py      图片、音频和文件的派生观察（后续）
├── extraction.py      从已完成轮次提取记忆候选（后续）
├── consolidation.py   ADD/UPDATE/MERGE/ARCHIVE 等整合（后续）
└── __init__.py        对外导出稳定的记忆层契约
~~~

当前已经落地 PostgreSQL 事实存储和 MemoryManager 调用门面。后续模块在对应阶段
实现时再增加，不预先创建空壳。

## 当前已实现

AgentMemoryModel 将四类记忆统一保存到 agent_memories 表，保存内容、作用域、来源、
状态、评分和有效期。PostgreSQLMemoryProvider 提供第一版的新增、关键词或精确读取、
更新和软归档；当前关键词读取使用 PostgreSQL ILIKE，还不等同于向量检索。

MemoryManager 是 Agent 节点的唯一调用入口。它提供 save_working、save_episodic、
save_semantic、save_perceptual 及对应读取方法；Agent 节点不需要直接导入 SQLAlchemy
模型。Working Memory 的读取会要求 conversation_id 精确匹配，长期记忆才允许按更宽的
作用域复用。

应用启动时由 PostgresClientManager 创建表模型对应的业务表和记忆 Provider；官方
LangGraph Checkpointer 表仍然由 AsyncPostgresSaver.setup() 管理，两者不混用。

建表脚本：scripts/postgres/14_create_agent_memory_tables.sql

## 四类记忆

| 类型 | 保存内容 | 默认读取方式 | 不保存什么 |
| --- | --- | --- | --- |
| Working | 当前会话的活动目标、最近消息摘要、未完成任务、活动资源引用 | 按 conversation_id 精确读取 | 无限制增长的完整历史 |
| Episodic | 已完成分析、成功或失败经验、可复用任务过程摘要 | 按问题相关性检索 | 全量 SSE、完整 rows、逐 Token 输出 |
| Semantic | 稳定业务事实、指标定义、项目约定、用户明确偏好 | 精确读取与语义检索结合 | 一次性问题、未经确认的推测 |
| Perceptual | 资源身份、OCR、版面、视觉观察、音频转写及来源 | asset_id 精确读取，必要时语义检索 | 原始二进制文件本身 |

原始图片、音频和文件由资源存储保存；Perceptual Memory 保存 `asset_id` 和派生观察。
需要重新判断颜色、位置或布局时，调用方通过 `asset_id` 重新加载原始资源。

## 与其他模块的边界

~~~text
PostgreSQL
    记忆事实、来源、状态、版本和生命周期

Qdrant
    可重建的 Semantic/Episodic/Perceptual 语义索引

LangGraph Checkpointer
    图执行恢复，不是长期记忆库

RAG
    外部知识库，不是用户记忆

app/agent/memory
    读取、写入、提取和整合 Data Agent 记忆

app/context_engine
    对候选上下文做隔离、选择、预算控制、结构化和追踪
~~~

记忆层不直接依赖 `ContextItem`。后续由 Data Agent 适配层把 `MemoryItem` 映射到
稳定的上下文分区：

~~~text
Working Memory     -> Active Conversation
Episodic Memory    -> Referenced History
Semantic Memory    -> Relevant Knowledge
Perceptual Memory  -> Referenced Resources
Runtime Evidence   -> 本轮真实完成的证据，不属于长期记忆
RAG Evidence       -> 外部知识证据，不属于用户记忆
~~~

## 请求与提交链路

~~~mermaid
flowchart TD
    A[用户请求] --> B[读取 Working Memory]
    B --> C{是否需要历史经验、稳定知识或历史资源}
    C -->|需要| D[检索 Episodic / Semantic / Perceptual]
    C -->|不需要| E[建立上下文候选]
    D --> E
    E --> F[ContextEngine 编译模型上下文]
    F --> G[Data Agent / LLM 执行]
    G --> H[保存消息、报告和真实运行证据]
    H --> I[提取长期记忆候选]
    I --> J[整合 ADD / UPDATE / MERGE / ARCHIVE / IGNORE / CONFLICT]
    J --> K[更新 Working 和长期记忆]
~~~

硬边界：请求开始时只能读取过去已经存在的事实；待执行计划不能冒充结果；节点完成后
真实结果才进入 Runtime Evidence；最终回答结束后才提取和整合长期记忆候选。

完整设计和阶段任务见：

- `docs/memory_context_architecture.md`
- `docs/memory_layer_todo.md`
