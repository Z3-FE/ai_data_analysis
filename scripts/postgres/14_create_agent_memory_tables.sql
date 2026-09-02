-- 14_create_agent_memory_tables.sql
-- 作用：创建 Data Agent 四类记忆共用的 PostgreSQL 事实表。
-- 记忆事实由本项目模型和脚本管理，LangGraph Checkpointer 表仍由 setup() 管理。

CREATE TABLE IF NOT EXISTS agent_memories (
  memory_id VARCHAR(128) PRIMARY KEY,
  memory_type VARCHAR(32) NOT NULL,
  content TEXT NOT NULL,

  user_id VARCHAR(128) NOT NULL,
  tenant_id VARCHAR(128),
  agent_id VARCHAR(128),
  project_id VARCHAR(128),
  conversation_id VARCHAR(128),

  source_type VARCHAR(64) NOT NULL,
  source_id VARCHAR(128),
  source_turn_id VARCHAR(128),
  source_message_id VARCHAR(128),
  source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

  structured_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,

  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,

  CONSTRAINT ck_agent_memories_type
    CHECK (memory_type IN ('working', 'episodic', 'semantic', 'perceptual')),
  CONSTRAINT ck_agent_memories_status
    CHECK (status IN ('active', 'archived', 'superseded', 'deleted', 'conflict')),
  CONSTRAINT ck_agent_memories_importance
    CHECK (importance >= 0 AND importance <= 1),
  CONSTRAINT ck_agent_memories_confidence
    CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT ck_agent_memories_valid_range
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)
);

COMMENT ON TABLE agent_memories IS 'Data Agent 四类记忆事实';
COMMENT ON COLUMN agent_memories.memory_id IS '记忆事实唯一 ID，也是外部索引回查 PostgreSQL 的主键';
COMMENT ON COLUMN agent_memories.memory_type IS '记忆类型：working、episodic、semantic 或 perceptual';
COMMENT ON COLUMN agent_memories.content IS '用于检索和组装模型上下文的自然语言记忆内容';
COMMENT ON COLUMN agent_memories.user_id IS '所属用户 ID，所有记忆的最低数据隔离边界';
COMMENT ON COLUMN agent_memories.tenant_id IS '所属租户 ID，用于多租户数据隔离；单租户阶段可以为空';
COMMENT ON COLUMN agent_memories.agent_id IS '所属 Agent ID，用于隔离 Data Agent 与其他 Agent 的记忆空间';
COMMENT ON COLUMN agent_memories.project_id IS '所属项目 ID，用于隔离同一用户的不同项目记忆';
COMMENT ON COLUMN agent_memories.conversation_id IS '所属会话 ID；为空表示允许跨会话复用的长期记忆';
COMMENT ON COLUMN agent_memories.source_type IS '记忆来源类别，例如 conversation_message、analysis_output 或 asset';
COMMENT ON COLUMN agent_memories.source_id IS '来源对象 ID，例如 message_id、turn_id 或 asset_id';
COMMENT ON COLUMN agent_memories.source_turn_id IS '产生该记忆的会话轮次 ID';
COMMENT ON COLUMN agent_memories.source_message_id IS '产生该记忆的原始消息 ID';
COMMENT ON COLUMN agent_memories.source_metadata IS '来源附加数据，例如提取器名称、版本和引用位置';
COMMENT ON COLUMN agent_memories.structured_data IS '按记忆类型扩展的结构化数据，例如 asset_id、task_id 或 preference_key';
COMMENT ON COLUMN agent_memories.metadata IS '存储、提取、索引和整合过程的元数据，不作为主要记忆内容';
COMMENT ON COLUMN agent_memories.status IS '记忆生命周期状态：active、archived、superseded、deleted 或 conflict';
COMMENT ON COLUMN agent_memories.importance IS '业务重要性评分，取值范围为 0 到 1';
COMMENT ON COLUMN agent_memories.confidence IS '记忆内容可信度评分，取值范围为 0 到 1';
COMMENT ON COLUMN agent_memories.created_at IS '记忆事实首次写入时间';
COMMENT ON COLUMN agent_memories.updated_at IS '记忆事实最近一次更新或整合时间';
COMMENT ON COLUMN agent_memories.valid_from IS '记忆事实开始生效的时间；为空表示未设置起始时间';
COMMENT ON COLUMN agent_memories.valid_to IS '记忆事实失效的时间；为空表示当前没有失效时间';

CREATE INDEX IF NOT EXISTS idx_agent_memories_scope
  ON agent_memories (user_id, tenant_id, agent_id, project_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_type_status
  ON agent_memories (memory_type, status);
CREATE INDEX IF NOT EXISTS idx_agent_memories_source
  ON agent_memories (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_source_turn
  ON agent_memories (source_turn_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_source_message
  ON agent_memories (source_message_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_structured_data
  ON agent_memories USING GIN (structured_data);
