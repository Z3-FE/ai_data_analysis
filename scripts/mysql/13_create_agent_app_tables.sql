-- 13_create_agent_app_tables.sql
-- 作用：创建会话历史和可渲染结果表。
-- 注意：本脚本只使用 CREATE TABLE IF NOT EXISTS，不删除历史会话。

USE agent_app;

CREATE TABLE IF NOT EXISTS conversations (
  conversation_id VARCHAR(128) PRIMARY KEY COMMENT '业务会话 ID',
  user_id VARCHAR(128) NOT NULL COMMENT '所属用户 ID',
  thread_id VARCHAR(128) NOT NULL UNIQUE COMMENT 'LangGraph thread_id，当前与 conversation_id 一致',
  title VARCHAR(255) NOT NULL DEFAULT '新建会话' COMMENT '会话标题',
  data_source_id VARCHAR(128) NOT NULL DEFAULT 'olist' COMMENT '会话使用的数据源',
  status VARCHAR(32) NOT NULL DEFAULT 'created' COMMENT 'created/running/completed/failed/cancelled',
  active_run_id VARCHAR(128) NULL COMMENT '当前运行 ID',
  metadata JSON NOT NULL COMMENT '创建会话时的应用元数据',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_conversations_user_updated (user_id, updated_at),
  KEY idx_conversations_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 应用会话';

CREATE TABLE IF NOT EXISTS conversation_turns (
  turn_id VARCHAR(128) PRIMARY KEY COMMENT '一次用户提问的轮次 ID',
  conversation_id VARCHAR(128) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  thread_id VARCHAR(128) NOT NULL,
  run_id VARCHAR(128) NOT NULL,
  input_text LONGTEXT NOT NULL COMMENT '用户原始问题',
  execution_mode VARCHAR(32) NULL COMMENT 'single_query/analysis/clarification',
  status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running/completed/failed/cancelled',
  error_message TEXT NULL COMMENT '失败原因',
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_turns_conversation_started (conversation_id, started_at),
  KEY idx_turns_user_started (user_id, started_at),
  KEY idx_turns_run (run_id),
  CONSTRAINT fk_turns_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 会话轮次';

CREATE TABLE IF NOT EXISTS conversation_messages (
  message_id VARCHAR(128) PRIMARY KEY COMMENT '消息 ID',
  conversation_id VARCHAR(128) NOT NULL,
  turn_id VARCHAR(128) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  role VARCHAR(32) NOT NULL COMMENT 'user/assistant/system',
  message_type VARCHAR(32) NOT NULL DEFAULT 'text' COMMENT '当前阶段只保存 text',
  sequence_no INT NOT NULL COMMENT '轮次内顺序，用户为 0，助手为 1',
  content LONGTEXT NOT NULL COMMENT '可直接展示的消息内容',
  metadata JSON NOT NULL COMMENT '消息状态等小型元数据',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_message_turn_sequence (turn_id, sequence_no),
  KEY idx_messages_conversation_created (conversation_id, created_at),
  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  CONSTRAINT fk_messages_turn FOREIGN KEY (turn_id) REFERENCES conversation_turns(turn_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 会话消息';

CREATE TABLE IF NOT EXISTS turn_outputs (
  output_id VARCHAR(128) PRIMARY KEY COMMENT '结构化输出 ID',
  conversation_id VARCHAR(128) NOT NULL,
  turn_id VARCHAR(128) NOT NULL,
  output_type VARCHAR(32) NOT NULL COMMENT 'query_result/rendered_report/clarification/failure',
  payload JSON NOT NULL COMMENT '可供前端重新渲染的受控结果',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_output_turn_type (turn_id, output_type),
  KEY idx_outputs_conversation_created (conversation_id, created_at),
  CONSTRAINT fk_outputs_conversation FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE,
  CONSTRAINT fk_outputs_turn FOREIGN KEY (turn_id) REFERENCES conversation_turns(turn_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Agent 轮次可渲染输出';
