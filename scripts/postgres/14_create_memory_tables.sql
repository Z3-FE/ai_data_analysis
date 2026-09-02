-- 14_create_memory_tables.sql
-- 作用：创建四类记忆共用的 PostgreSQL 事实表和可重建索引任务表。
-- Working Memory 由 AgentState.messages 与 LangGraph Checkpointer 承担，不在这里重复保存。
-- Qdrant 和 Neo4j 只保存投影，PostgreSQL 中的记录可用于重建这些投影。

CREATE TABLE IF NOT EXISTS agent_memories (
  memory_id VARCHAR(128) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  memory_type VARCHAR(32) NOT NULL,
  scope VARCHAR(32) NOT NULL DEFAULT 'user',
  conversation_id VARCHAR(128),
  project_id VARCHAR(128),
  content TEXT NOT NULL,
  structured_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  version INTEGER NOT NULL DEFAULT 1,
  supersedes_memory_id VARCHAR(128),
  expires_at TIMESTAMP,
  access_count INTEGER NOT NULL DEFAULT 0,
  last_accessed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- CREATE TABLE IF NOT EXISTS 不会给早期表补列；重复执行时完成幂等升级。
ALTER TABLE agent_memories
  ADD COLUMN IF NOT EXISTS project_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS idx_agent_memories_user_type_status
  ON agent_memories (user_id, memory_type, status);
CREATE INDEX IF NOT EXISTS idx_agent_memories_conversation
  ON agent_memories (conversation_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_project
  ON agent_memories (project_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_supersedes
  ON agent_memories (supersedes_memory_id);
CREATE INDEX IF NOT EXISTS idx_agent_memories_expires
  ON agent_memories (expires_at);

COMMENT ON TABLE agent_memories IS 'Agent 长期记忆事实主表';
COMMENT ON COLUMN agent_memories.memory_id IS '记忆记录 ID，也是 Qdrant point_id 来源';
COMMENT ON COLUMN agent_memories.user_id IS '所属用户 ID，用于跨会话隔离';
COMMENT ON COLUMN agent_memories.memory_type IS 'episodic/semantic/perceptual';
COMMENT ON COLUMN agent_memories.scope IS '记忆范围：user/conversation/project';
COMMENT ON COLUMN agent_memories.conversation_id IS '产生记忆的会话 ID';
COMMENT ON COLUMN agent_memories.project_id IS '项目级记忆所属项目 ID';
COMMENT ON COLUMN agent_memories.content IS '可检索和展示的记忆正文';
COMMENT ON COLUMN agent_memories.structured_data IS '结构化事实、任务结果或模态元数据';
COMMENT ON COLUMN agent_memories.status IS 'active/superseded/forgotten/expired';
COMMENT ON COLUMN agent_memories.importance IS '记忆重要性，约定范围 0 到 1';
COMMENT ON COLUMN agent_memories.confidence IS '记忆可信度，约定范围 0 到 1';
COMMENT ON COLUMN agent_memories.version IS '同一逻辑记忆的版本号';
COMMENT ON COLUMN agent_memories.supersedes_memory_id IS '被当前记录替代的旧记忆 ID';
COMMENT ON COLUMN agent_memories.expires_at IS '记忆过期时间，为空表示不自动过期';
COMMENT ON COLUMN agent_memories.access_count IS '被检索命中的次数';
COMMENT ON COLUMN agent_memories.last_accessed_at IS '最近一次被检索命中的时间';
COMMENT ON COLUMN agent_memories.created_at IS '记录创建时间';
COMMENT ON COLUMN agent_memories.updated_at IS '记录最近更新时间';

CREATE TABLE IF NOT EXISTS memory_sources (
  source_link_id VARCHAR(128) PRIMARY KEY,
  memory_id VARCHAR(128) NOT NULL REFERENCES agent_memories(memory_id) ON DELETE CASCADE,
  source_type VARCHAR(32) NOT NULL,
  source_id VARCHAR(128) NOT NULL,
  source_path VARCHAR(512),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_memory_source UNIQUE (memory_id, source_type, source_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_sources_memory ON memory_sources (memory_id);
COMMENT ON TABLE memory_sources IS '记忆来源关联';
COMMENT ON COLUMN memory_sources.source_link_id IS '来源关联记录 ID';
COMMENT ON COLUMN memory_sources.memory_id IS '被引用的记忆 ID';
COMMENT ON COLUMN memory_sources.source_type IS 'message/turn/output/asset';
COMMENT ON COLUMN memory_sources.source_id IS '来源对象 ID';
COMMENT ON COLUMN memory_sources.source_path IS '来源对象中的可选字段路径';
COMMENT ON COLUMN memory_sources.created_at IS '来源关联创建时间';

CREATE TABLE IF NOT EXISTS memory_assets (
  asset_id VARCHAR(128) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  conversation_id VARCHAR(128),
  project_id VARCHAR(128),
  modality VARCHAR(32) NOT NULL,
  file_name VARCHAR(512) NOT NULL,
  mime_type VARCHAR(128) NOT NULL,
  storage_uri VARCHAR(2048) NOT NULL,
  extracted_text TEXT,
  extraction_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  encoder_name VARCHAR(255),
  embedding_dimension INTEGER,
  index_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 重复执行脚本时为早期表补充项目作用域。
ALTER TABLE memory_assets
  ADD COLUMN IF NOT EXISTS project_id VARCHAR(128);

CREATE INDEX IF NOT EXISTS idx_memory_assets_user_conversation
  ON memory_assets (user_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_memory_assets_project
  ON memory_assets (project_id);
CREATE INDEX IF NOT EXISTS idx_memory_assets_modality_status
  ON memory_assets (modality, extraction_status, index_status);
COMMENT ON TABLE memory_assets IS '记忆层附件元数据';
COMMENT ON COLUMN memory_assets.asset_id IS '附件稳定 ID，也是历史引用对象 ID';
COMMENT ON COLUMN memory_assets.user_id IS '上传用户 ID';
COMMENT ON COLUMN memory_assets.conversation_id IS '附件所属会话 ID';
COMMENT ON COLUMN memory_assets.project_id IS '项目级附件所属项目 ID';
COMMENT ON COLUMN memory_assets.modality IS 'text/image/audio/video';
COMMENT ON COLUMN memory_assets.file_name IS '原始文件名';
COMMENT ON COLUMN memory_assets.mime_type IS '文件 MIME 类型';
COMMENT ON COLUMN memory_assets.storage_uri IS '本地路径或对象存储 URI';
COMMENT ON COLUMN memory_assets.extracted_text IS '从附件提取的可检索文本';
COMMENT ON COLUMN memory_assets.extraction_status IS 'pending/completed/failed/unsupported';
COMMENT ON COLUMN memory_assets.encoder_name IS '向量编码器名称';
COMMENT ON COLUMN memory_assets.embedding_dimension IS '向量维度';
COMMENT ON COLUMN memory_assets.index_status IS 'pending/completed/failed';
COMMENT ON COLUMN memory_assets.metadata IS '附件大小、校验和等扩展元数据';
COMMENT ON COLUMN memory_assets.created_at IS '附件记录创建时间';
COMMENT ON COLUMN memory_assets.updated_at IS '附件记录最近更新时间';

CREATE TABLE IF NOT EXISTS memory_graph_projections (
  projection_id VARCHAR(128) PRIMARY KEY,
  memory_id VARCHAR(128) NOT NULL UNIQUE REFERENCES agent_memories(memory_id) ON DELETE CASCADE,
  entities JSONB NOT NULL DEFAULT '[]'::jsonb,
  relations JSONB NOT NULL DEFAULT '[]'::jsonb,
  sync_status VARCHAR(32) NOT NULL DEFAULT 'pending',
  last_error TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_graph_projections_status
  ON memory_graph_projections (sync_status);
COMMENT ON TABLE memory_graph_projections IS 'Semantic Memory 图投影事实记录';
COMMENT ON COLUMN memory_graph_projections.projection_id IS '图投影记录 ID';
COMMENT ON COLUMN memory_graph_projections.memory_id IS '对应 Semantic Memory ID';
COMMENT ON COLUMN memory_graph_projections.entities IS 'Neo4j 实体节点重建输入';
COMMENT ON COLUMN memory_graph_projections.relations IS 'Neo4j 实体关系重建输入';
COMMENT ON COLUMN memory_graph_projections.sync_status IS 'pending/completed/failed';
COMMENT ON COLUMN memory_graph_projections.last_error IS '最近一次图同步错误';
COMMENT ON COLUMN memory_graph_projections.created_at IS '投影记录创建时间';
COMMENT ON COLUMN memory_graph_projections.updated_at IS '投影记录最近更新时间';

CREATE TABLE IF NOT EXISTS memory_index_jobs (
  job_id VARCHAR(128) PRIMARY KEY,
  memory_id VARCHAR(128) NOT NULL REFERENCES agent_memories(memory_id) ON DELETE CASCADE,
  target VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memory_index_jobs_status
  ON memory_index_jobs (status, target, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS uk_memory_index_jobs_active
  ON memory_index_jobs (memory_id, target)
  WHERE status IN ('pending', 'processing');
COMMENT ON TABLE memory_index_jobs IS '记忆检索索引同步任务';
COMMENT ON COLUMN memory_index_jobs.job_id IS '索引任务 ID';
COMMENT ON COLUMN memory_index_jobs.memory_id IS '待同步记忆 ID';
COMMENT ON COLUMN memory_index_jobs.target IS 'qdrant 或 neo4j';
COMMENT ON COLUMN memory_index_jobs.status IS 'pending/processing/completed/failed';
COMMENT ON COLUMN memory_index_jobs.attempts IS '已重试次数';
COMMENT ON COLUMN memory_index_jobs.last_error IS '最近一次执行错误';
COMMENT ON COLUMN memory_index_jobs.created_at IS '任务创建时间';
COMMENT ON COLUMN memory_index_jobs.updated_at IS '任务最近更新时间';
