-- 13_create_agent_app_tables.sql
-- 作用：在 PostgreSQL agent_app 中创建会话历史和可渲染结果表。
-- LangGraph Checkpointer 的表由 AsyncPostgresSaver.setup() 管理，不在本脚本重复定义。

CREATE TABLE IF NOT EXISTS conversations (
  conversation_id VARCHAR(128) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  thread_id VARCHAR(128) NOT NULL UNIQUE,
  title VARCHAR(255) NOT NULL DEFAULT '新建会话',
  data_source_id VARCHAR(128) NOT NULL DEFAULT 'olist',
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  active_run_id VARCHAR(128),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
  ON conversations (user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_conversations_status
  ON conversations (status);

COMMENT ON TABLE conversations IS 'Agent 应用会话';
COMMENT ON COLUMN conversations.conversation_id IS '业务会话 ID';
COMMENT ON COLUMN conversations.user_id IS '所属用户 ID';
COMMENT ON COLUMN conversations.thread_id IS 'LangGraph thread_id，当前与 conversation_id 一致';
COMMENT ON COLUMN conversations.title IS '会话标题';
COMMENT ON COLUMN conversations.data_source_id IS '会话使用的数据源';
COMMENT ON COLUMN conversations.status IS 'created/running/completed/failed/cancelled';
COMMENT ON COLUMN conversations.active_run_id IS '当前运行 ID';
COMMENT ON COLUMN conversations.metadata IS '创建会话时的应用元数据';
COMMENT ON COLUMN conversations.created_at IS '创建时间';
COMMENT ON COLUMN conversations.updated_at IS '最后更新时间';

CREATE TABLE IF NOT EXISTS conversation_turns (
  turn_id VARCHAR(128) PRIMARY KEY,
  conversation_id VARCHAR(128) NOT NULL
    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  user_id VARCHAR(128) NOT NULL,
  thread_id VARCHAR(128) NOT NULL,
  run_id VARCHAR(128) NOT NULL,
  input_text TEXT NOT NULL,
  execution_mode VARCHAR(32),
  status VARCHAR(32) NOT NULL DEFAULT 'running',
  error_message TEXT,
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_turns_conversation_started
  ON conversation_turns (conversation_id, started_at);
CREATE INDEX IF NOT EXISTS idx_turns_user_started
  ON conversation_turns (user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_turns_run
  ON conversation_turns (run_id);

COMMENT ON TABLE conversation_turns IS 'Agent 会话轮次';
COMMENT ON COLUMN conversation_turns.turn_id IS '一次用户提问的轮次 ID';
COMMENT ON COLUMN conversation_turns.conversation_id IS '所属业务会话 ID';
COMMENT ON COLUMN conversation_turns.user_id IS '所属用户 ID';
COMMENT ON COLUMN conversation_turns.thread_id IS 'LangGraph thread_id';
COMMENT ON COLUMN conversation_turns.run_id IS '本轮 Agent 执行 ID';
COMMENT ON COLUMN conversation_turns.input_text IS '用户原始问题';
COMMENT ON COLUMN conversation_turns.execution_mode IS 'single_query/analysis/clarification';
COMMENT ON COLUMN conversation_turns.status IS 'running/completed/failed/cancelled';
COMMENT ON COLUMN conversation_turns.error_message IS '失败原因';
COMMENT ON COLUMN conversation_turns.started_at IS '开始执行时间';
COMMENT ON COLUMN conversation_turns.completed_at IS '完成执行时间';
COMMENT ON COLUMN conversation_turns.created_at IS '记录创建时间';

CREATE TABLE IF NOT EXISTS conversation_messages (
  message_id VARCHAR(128) PRIMARY KEY,
  conversation_id VARCHAR(128) NOT NULL
    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  turn_id VARCHAR(128) NOT NULL
    REFERENCES conversation_turns(turn_id) ON DELETE CASCADE,
  user_id VARCHAR(128) NOT NULL,
  role VARCHAR(32) NOT NULL,
  message_type VARCHAR(32) NOT NULL DEFAULT 'text',
  sequence_no INTEGER NOT NULL,
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_message_turn_sequence UNIQUE (turn_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
  ON conversation_messages (conversation_id, created_at);

COMMENT ON TABLE conversation_messages IS 'Agent 会话消息';
COMMENT ON COLUMN conversation_messages.message_id IS '消息 ID';
COMMENT ON COLUMN conversation_messages.conversation_id IS '所属业务会话 ID';
COMMENT ON COLUMN conversation_messages.turn_id IS '所属会话轮次 ID';
COMMENT ON COLUMN conversation_messages.user_id IS '所属用户 ID';
COMMENT ON COLUMN conversation_messages.role IS 'user/assistant/system';
COMMENT ON COLUMN conversation_messages.message_type IS '当前阶段只保存 text';
COMMENT ON COLUMN conversation_messages.sequence_no IS '轮次内顺序，用户为 0，助手为 1';
COMMENT ON COLUMN conversation_messages.content IS '可直接展示的消息内容';
COMMENT ON COLUMN conversation_messages.metadata IS '消息状态等小型元数据';
COMMENT ON COLUMN conversation_messages.created_at IS '消息创建时间';

CREATE TABLE IF NOT EXISTS turn_outputs (
  output_id VARCHAR(128) PRIMARY KEY,
  conversation_id VARCHAR(128) NOT NULL
    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  turn_id VARCHAR(128) NOT NULL
    REFERENCES conversation_turns(turn_id) ON DELETE CASCADE,
  output_type VARCHAR(32) NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_output_turn_type UNIQUE (turn_id, output_type)
);

CREATE INDEX IF NOT EXISTS idx_outputs_conversation_created
  ON turn_outputs (conversation_id, created_at);

COMMENT ON TABLE turn_outputs IS 'Agent 轮次可渲染输出';
COMMENT ON COLUMN turn_outputs.output_id IS '结构化输出 ID';
COMMENT ON COLUMN turn_outputs.conversation_id IS '所属业务会话 ID';
COMMENT ON COLUMN turn_outputs.turn_id IS '所属会话轮次 ID';
COMMENT ON COLUMN turn_outputs.output_type IS 'query_result/rendered_report/execution_trace/clarification/failure';
COMMENT ON COLUMN turn_outputs.payload IS '可供前端重新渲染的受控结果';
COMMENT ON COLUMN turn_outputs.created_at IS '输出创建时间';
COMMENT ON COLUMN turn_outputs.updated_at IS '输出最后更新时间';
