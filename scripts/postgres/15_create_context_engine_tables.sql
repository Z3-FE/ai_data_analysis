-- 15_create_context_engine_tables.sql
-- 作用：创建 ContextEngine 的增量会话摘要和紧凑构建审计表。
-- 本脚本只管理业务表；LangGraph Checkpointer 官方表仍由 AsyncPostgresSaver.setup() 管理。

CREATE TABLE IF NOT EXISTS context_conversation_summaries (
  summary_id VARCHAR(128) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  conversation_id VARCHAR(128) NOT NULL
    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  covered_from_index INTEGER NOT NULL,
  covered_through_index INTEGER NOT NULL,
  source_message_count INTEGER NOT NULL,
  token_count INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_context_summary_user_conversation
    UNIQUE (user_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_context_summaries_user_updated
  ON context_conversation_summaries (user_id, updated_at);

COMMENT ON TABLE context_conversation_summaries IS 'ContextEngine 会话增量摘要';
COMMENT ON COLUMN context_conversation_summaries.summary_id IS '摘要稳定 ID';
COMMENT ON COLUMN context_conversation_summaries.user_id IS '摘要所属用户 ID';
COMMENT ON COLUMN context_conversation_summaries.conversation_id IS '摘要所属业务会话 ID';
COMMENT ON COLUMN context_conversation_summaries.content IS '较早会话的高保真摘要，不含隐藏思考';
COMMENT ON COLUMN context_conversation_summaries.covered_from_index IS '摘要覆盖的第一条 Working Message 序号';
COMMENT ON COLUMN context_conversation_summaries.covered_through_index IS '摘要覆盖的最后一条 Working Message 序号';
COMMENT ON COLUMN context_conversation_summaries.source_message_count IS '摘要累计覆盖的原始消息数量';
COMMENT ON COLUMN context_conversation_summaries.token_count IS '摘要正文的 tokenizer token 数';
COMMENT ON COLUMN context_conversation_summaries.version IS '摘要增量更新版本';
COMMENT ON COLUMN context_conversation_summaries.created_at IS '首次形成摘要的时间';
COMMENT ON COLUMN context_conversation_summaries.updated_at IS '最近一次扩展摘要覆盖范围的时间';

CREATE TABLE IF NOT EXISTS context_build_runs (
  build_id VARCHAR(128) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  conversation_id VARCHAR(128) NOT NULL
    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  agent_type VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  query_hash VARCHAR(64) NOT NULL,
  token_budget INTEGER NOT NULL,
  final_token_count INTEGER NOT NULL DEFAULT 0,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  selected_count INTEGER NOT NULL DEFAULT 0,
  retrieval_plan JSONB NOT NULL DEFAULT '{}'::jsonb,
  reference_resolution JSONB NOT NULL DEFAULT '{}'::jsonb,
  decisions JSONB NOT NULL DEFAULT '[]'::jsonb,
  summary_updated BOOLEAN NOT NULL DEFAULT FALSE,
  error_message TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_context_builds_user_conversation
  ON context_build_runs (user_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_builds_status_created
  ON context_build_runs (status, created_at);

COMMENT ON TABLE context_build_runs IS 'ContextEngine 构建审计';
COMMENT ON COLUMN context_build_runs.build_id IS '单次上下文构建 ID';
COMMENT ON COLUMN context_build_runs.user_id IS '构建所属用户 ID';
COMMENT ON COLUMN context_build_runs.conversation_id IS '构建所属业务会话 ID';
COMMENT ON COLUMN context_build_runs.agent_type IS '消费上下文的 Agent 类型';
COMMENT ON COLUMN context_build_runs.status IS 'pending/completed/failed';
COMMENT ON COLUMN context_build_runs.query_hash IS '用户原问题 SHA-256，不保存原问题正文';
COMMENT ON COLUMN context_build_runs.token_budget IS '本轮最大上下文 token 预算';
COMMENT ON COLUMN context_build_runs.final_token_count IS '最终编译消息的真实 token 数';
COMMENT ON COLUMN context_build_runs.candidate_count IS 'Gather 阶段候选总数';
COMMENT ON COLUMN context_build_runs.selected_count IS '最终进入模型上下文的候选数';
COMMENT ON COLUMN context_build_runs.retrieval_plan IS '不含检索问题正文的召回计划';
COMMENT ON COLUMN context_build_runs.reference_resolution IS '历史依赖、附件 ID 和未解析引用';
COMMENT ON COLUMN context_build_runs.decisions IS '候选 ID、分数、token、原因和来源 ID，不含正文';
COMMENT ON COLUMN context_build_runs.summary_updated IS '本轮是否扩展了会话摘要';
COMMENT ON COLUMN context_build_runs.error_message IS '构建失败说明';
COMMENT ON COLUMN context_build_runs.created_at IS '开始构建时间';
COMMENT ON COLUMN context_build_runs.completed_at IS '构建完成或失败时间';
