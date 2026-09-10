# Data Agent Harness 开发 TODO

> 当前只登记现有 Agent 的能力基线。这里的内容用于确认 Harness 将来需要承接什么，不代表这些能力已经组成完整 Harness。Harness 的边界、开发顺序、工具协议和迁移方式，待单独确认后再补充。

## 当前 Agent 已有能力

### 会话与状态

- [x] 创建、切换和加载业务会话。
- [x] 保存用户消息、助手最终消息和结构化输出。
- [x] 区分 `user_id`、`conversation_id`、`turn_id` 和 `run_id`。
- [x] 使用 `conversation_id` 作为 LangGraph `thread_id`。
- [x] 使用 PostgreSQL 中的 LangGraph Checkpointer 保存线程状态。

### 当前问题路由

- [x] `daily_chat`：处理问候、能力介绍和一般对话。
- [x] `single_query`：进入一次查询即可回答的问数链路。
- [x] `analysis`：进入多任务分析、计算和报告链路。
- [x] `clarification`：缺少必要信息时返回澄清问题。
- [x] 通过现有固定 LangGraph 图连接上述执行分支。

### 数据目录与元数据理解

- [x] 检索指标、指标定义和指标相关字段。
- [x] 检索表、字段、维度和维度值。
- [x] 检索表关系、指标与维度关系以及查询所需的元数据上下文。
- [x] 关联用户问题中的业务术语和 Meta 库中的正式名称。
- [x] 使用 Meta 数据和向量检索辅助数据问题理解。

### 简单问数

- [x] 从问题中提取关键词、指标和查询条件。
- [x] 召回相关指标、表、字段、维度和维度值。
- [x] 合并元数据上下文并生成 SQL。
- [x] 执行只读 SQL 查询。
- [x] 校验结果字段并处理维度值映射。
- [x] 返回文字、数值、表格或简单图表数据。

### 数据分析

- [x] 从复杂问题中生成分析目标和分析计划。
- [x] 拆分多个分析任务并处理任务依赖。
- [x] 在分析任务中复用问数链路获取数据。
- [x] 使用 Python 对查询结果进行计算、比较和分析。
- [x] 保存任务结果、分析证据、失败任务和限制说明。
- [x] 根据分析证据生成面向用户的分析总结。

### 报告与前端输出

- [x] 根据查询结果或分析证据生成报告规划。
- [x] 将报告规划绑定到真实任务结果。
- [x] 生成可重新渲染的 `RenderedReport`。
- [x] 支持 KPI、表格、图表、布局和限制说明。
- [x] 通过 SSE 返回节点、任务、报告和失败状态。
- [x] 前端按消息类型渲染文本、数值、表格、图表和报告。
- [x] 支持查看对应轮次的查询或分析执行过程。

### 记忆与上下文基础

- [x] 已建立 Working、Episodic、Semantic 和 Perceptual Memory 的领域边界。
- [x] 使用 PostgreSQL 保存长期记忆事实，使用 Qdrant 和 Neo4j 保存检索投影。
- [x] 在轮次结束后进行长期记忆候选提取、治理、去重和版本处理。
- [x] 已建立独立的 `app/agent/context_engine/` 上下文工程模块。
- [x] ContextEngine 当前具备历史读取、记忆召回、附件引用、摘要、压缩、选择和上下文编译能力。
- [ ] ContextEngine 尚未接入 Agent 主执行链。

### 执行控制与观测基础

- [x] 输出节点级、任务级和报告级执行事件。
- [x] 合并流式思考和回答片段，避免每个 chunk 都成为独立历史事件。
- [x] 记录节点失败、任务失败、超时和受控错误信息。
- [x] 保存有限的执行轨迹，支持前端查看执行详情。
- [ ] 尚未形成统一的 Planning Agent、工具调用协议和 Harness Loop Controller。
- [ ] 当前没有由 Harness 统一承接 ContextEngine、规划、工具执行和最终收尾。

## 已确认的 Harness 核心概念

`Data Agent Harness` 是组织 Agent 完整运行的框架，`Loop Controller` 是其中的调度核心。

```text
用户请求
  ↓
ContextEngine 准备上下文
  ↓
Loop Controller 调用 Planning Agent
  ↓
校验计划并通过 Tool Runtime 执行工具
  ↓
接收 ToolResult，更新状态并再次规划
  ↓
继续、澄清或结束
```

职责边界：

```text
Planning Agent：提出下一步计划，不直接执行业务逻辑。
Loop Controller：调度规划、工具执行、状态更新和循环结束。
Tool Runtime：执行工具调用的权限、参数、超时和结果控制。
业务工具：实现问数、分析、报告和知识检索等具体能力。
```

- [ ] 现有固定 Agent 图和业务节点继续保留，后续由 Harness 逐步承接。
