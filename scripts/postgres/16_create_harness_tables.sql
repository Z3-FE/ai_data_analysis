-- 16_create_harness_tables.sql
-- 作用：创建 Data Agent Harness 的运行、动作、确认和结果持久化表。
-- 本脚本只管理 Harness 业务表，不创建或修改 LangGraph Checkpointer 官方表。
-- 执行顺序：13_create_agent_app_tables.sql、14_create_memory_tables.sql、
-- 15_create_context_engine_tables.sql、16_create_harness_tables.sql。

CREATE TABLE IF NOT EXISTS harness_runs (
  run_id VARCHAR(128) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  conversation_id VARCHAR(128) NOT NULL,
  thread_id VARCHAR(128) NOT NULL,
  turn_id VARCHAR(128) NOT NULL,
  input_text TEXT NOT NULL,
  project_id VARCHAR(128),
  asset_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  status VARCHAR(32) NOT NULL,
  phase VARCHAR(32) NOT NULL,
  action_seq INTEGER NOT NULL DEFAULT 0,
  iteration INTEGER NOT NULL DEFAULT 0,
  state_version INTEGER NOT NULL DEFAULT 0,
  pending_confirmation_id VARCHAR(128),
  state_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_harness_runs_user_status
  ON harness_runs (user_id, status);
CREATE INDEX IF NOT EXISTS idx_harness_runs_conversation
  ON harness_runs (conversation_id);

COMMENT ON TABLE harness_runs IS 'Data Agent Harness 运行状态';
COMMENT ON COLUMN harness_runs.run_id IS 'Harness 运行 ID，也是恢复接口的外部引用';
COMMENT ON COLUMN harness_runs.user_id IS '用户隔离字段，读取和恢复时必须校验';
COMMENT ON COLUMN harness_runs.conversation_id IS '业务会话 ID';
COMMENT ON COLUMN harness_runs.thread_id IS 'LangGraph thread_id';
COMMENT ON COLUMN harness_runs.turn_id IS '当前用户轮次 ID';
COMMENT ON COLUMN harness_runs.input_text IS '用户原始问题';
COMMENT ON COLUMN harness_runs.project_id IS '数据项目范围';
COMMENT ON COLUMN harness_runs.asset_ids IS '本轮附件 ID 列表';
COMMENT ON COLUMN harness_runs.status IS 'Harness 生命周期状态';
COMMENT ON COLUMN harness_runs.phase IS 'Harness 当前控制阶段';
COMMENT ON COLUMN harness_runs.action_seq IS '已提交动作序号';
COMMENT ON COLUMN harness_runs.iteration IS '工具循环次数';
COMMENT ON COLUMN harness_runs.state_version IS 'Harness 状态版本，用于并发写入保护';
COMMENT ON COLUMN harness_runs.pending_confirmation_id IS '当前等待的确认 ID';
COMMENT ON COLUMN harness_runs.state_payload IS '可恢复的 JSON-safe Harness 状态';
COMMENT ON COLUMN harness_runs.created_at IS '创建时间';
COMMENT ON COLUMN harness_runs.updated_at IS '最近状态更新时间';

CREATE TABLE IF NOT EXISTS harness_actions (
  action_record_id VARCHAR(128) PRIMARY KEY,
  run_id VARCHAR(128) NOT NULL
    REFERENCES harness_runs(run_id) ON DELETE CASCADE,
  action_seq INTEGER NOT NULL,
  action_id VARCHAR(256),
  action_type VARCHAR(32) NOT NULL,
  action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload_digest VARCHAR(64) NOT NULL,
  commit_stage VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_harness_action_sequence UNIQUE (run_id, action_seq)
);

CREATE INDEX IF NOT EXISTS idx_harness_actions_run_stage
  ON harness_actions (run_id, commit_stage);

COMMENT ON TABLE harness_actions IS 'Data Agent Harness 动作提交记录';
COMMENT ON COLUMN harness_actions.action_record_id IS '动作记录 ID';
COMMENT ON COLUMN harness_actions.run_id IS '所属 Harness 运行';
COMMENT ON COLUMN harness_actions.action_seq IS '服务端发行的单调递增序号';
COMMENT ON COLUMN harness_actions.action_id IS '工具动作 ID，final_answer 和 ask_user 可以为空';
COMMENT ON COLUMN harness_actions.action_type IS 'tool_call/ask_user/final_answer';
COMMENT ON COLUMN harness_actions.action_payload IS '受控动作 JSON，不保存模型原始输出和隐藏思考';
COMMENT ON COLUMN harness_actions.payload_digest IS '动作内容摘要哈希';
COMMENT ON COLUMN harness_actions.commit_stage IS 'prepared/checkpoint/committed';
COMMENT ON COLUMN harness_actions.created_at IS '创建时间';
COMMENT ON COLUMN harness_actions.updated_at IS '最近阶段更新时间';

CREATE TABLE IF NOT EXISTS harness_confirmations (
  confirmation_id VARCHAR(128) PRIMARY KEY,
  run_id VARCHAR(128) NOT NULL
    REFERENCES harness_runs(run_id) ON DELETE CASCADE,
  action_seq INTEGER NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  conversation_id VARCHAR(128) NOT NULL,
  question TEXT NOT NULL,
  reason_code VARCHAR(64) NOT NULL,
  required_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
  choices JSONB NOT NULL DEFAULT '[]'::jsonb,
  request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  request_digest VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  visibility VARCHAR(32) NOT NULL,
  reply_payload JSONB,
  reply_digest VARCHAR(64),
  expires_at TIMESTAMP,
  prepared_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at TIMESTAMP,
  resolved_at TIMESTAMP,
  CONSTRAINT uk_harness_confirmation_action UNIQUE (run_id, action_seq)
);

CREATE INDEX IF NOT EXISTS idx_harness_confirmations_run_status
  ON harness_confirmations (run_id, status);
CREATE INDEX IF NOT EXISTS idx_harness_confirmations_user_status
  ON harness_confirmations (user_id, status);

COMMENT ON TABLE harness_confirmations IS 'Data Agent Harness 用户确认';
COMMENT ON COLUMN harness_confirmations.confirmation_id IS '服务端生成的确认请求 ID';
COMMENT ON COLUMN harness_confirmations.run_id IS '所属 Harness 运行';
COMMENT ON COLUMN harness_confirmations.action_seq IS '确认请求对应的动作序号';
COMMENT ON COLUMN harness_confirmations.user_id IS '用户隔离字段';
COMMENT ON COLUMN harness_confirmations.conversation_id IS '会话隔离字段';
COMMENT ON COLUMN harness_confirmations.question IS '展示给用户的问题';
COMMENT ON COLUMN harness_confirmations.reason_code IS '缺少条件、歧义或口径冲突等原因';
COMMENT ON COLUMN harness_confirmations.required_fields IS '允许用户补充的条件字段名';
COMMENT ON COLUMN harness_confirmations.choices IS '可选项列表';
COMMENT ON COLUMN harness_confirmations.request_payload IS '完整确认请求 JSON';
COMMENT ON COLUMN harness_confirmations.request_digest IS '请求内容哈希';
COMMENT ON COLUMN harness_confirmations.status IS 'pending/confirmed/rejected';
COMMENT ON COLUMN harness_confirmations.visibility IS 'prepared/published';
COMMENT ON COLUMN harness_confirmations.reply_payload IS '用户回复 JSON';
COMMENT ON COLUMN harness_confirmations.reply_digest IS '回复内容哈希';
COMMENT ON COLUMN harness_confirmations.expires_at IS '确认请求过期时间';
COMMENT ON COLUMN harness_confirmations.prepared_at IS '请求准备时间';
COMMENT ON COLUMN harness_confirmations.published_at IS '请求对前端可见时间';
COMMENT ON COLUMN harness_confirmations.resolved_at IS '用户处理完成时间';

CREATE TABLE IF NOT EXISTS harness_artifacts (
  artifact_id VARCHAR(64) PRIMARY KEY,
  result_ref VARCHAR(256) NOT NULL UNIQUE,
  run_id VARCHAR(128) NOT NULL
    REFERENCES harness_runs(run_id) ON DELETE CASCADE,
  user_id VARCHAR(128) NOT NULL,
  conversation_id VARCHAR(128) NOT NULL,
  thread_id VARCHAR(128) NOT NULL,
  turn_id VARCHAR(128) NOT NULL,
  action_id VARCHAR(256) NOT NULL,
  tool_name VARCHAR(128) NOT NULL,
  artifact_kind VARCHAR(64) NOT NULL,
  payload JSONB NOT NULL,
  payload_hash VARCHAR(64) NOT NULL,
  size_bytes INTEGER NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_artifacts_user_created
  ON harness_artifacts (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_harness_artifacts_run_created
  ON harness_artifacts (run_id, created_at);

COMMENT ON TABLE harness_artifacts IS 'Data Agent Harness 工具结果 Artifact';
COMMENT ON COLUMN harness_artifacts.artifact_id IS '完整 JSON payload 的 SHA-256 内容 ID';
COMMENT ON COLUMN harness_artifacts.result_ref IS '不透明结果引用，不能单独作为读取授权';
COMMENT ON COLUMN harness_artifacts.run_id IS '所属 Harness 运行';
COMMENT ON COLUMN harness_artifacts.user_id IS '用户隔离字段';
COMMENT ON COLUMN harness_artifacts.conversation_id IS '业务会话隔离字段';
COMMENT ON COLUMN harness_artifacts.thread_id IS 'LangGraph 线程隔离字段';
COMMENT ON COLUMN harness_artifacts.turn_id IS '当前轮次隔离字段';
COMMENT ON COLUMN harness_artifacts.action_id IS '产生结果的已提交动作 ID';
COMMENT ON COLUMN harness_artifacts.tool_name IS '产生结果的工具规范名称';
COMMENT ON COLUMN harness_artifacts.artifact_kind IS '结果类型，例如 query_result';
COMMENT ON COLUMN harness_artifacts.payload IS '完整结果内容，由 ArtifactStore 受控读取';
COMMENT ON COLUMN harness_artifacts.payload_hash IS 'payload 的规范 JSON SHA-256';
COMMENT ON COLUMN harness_artifacts.size_bytes IS 'payload 序列化后的 UTF-8 字节大小';
COMMENT ON COLUMN harness_artifacts.created_at IS '创建时间';
COMMENT ON COLUMN harness_artifacts.expires_at IS '过期时间';
