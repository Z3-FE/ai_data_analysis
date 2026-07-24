-- 01_reset_databases.sql
-- 作用：
--   初始化本项目的 MySQL 数据库环境。
--   每次导入前都会删除并重建 dw 和 meta，保证导入结果干净、可重复。
-- 输入：
--   无。只依赖当前 MySQL 实例。
-- 输出：
--   1. dw 数据库：存放维度表 dim_* 和事实表 fact_*。
--   2. meta 数据库：存放指标、维度、表关系、主题域等语义元数据。
-- 注意：
--   这是破坏性脚本，会删除已有 dw、meta、ai_data_analysis 数据库。
--   ai_data_analysis 是早期实验库，现在正式方案只保留 dw 和 meta。

-- 删除旧库，确保每次执行导入脚本时都是从零开始。
DROP DATABASE IF EXISTS dw;
DROP DATABASE IF EXISTS meta;
DROP DATABASE IF EXISTS ai_data_analysis;

-- 创建数据仓库库：真实业务分析表放这里。
CREATE DATABASE dw
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

-- 创建元数据库：AI 理解数据所需的说明书放这里。
CREATE DATABASE meta
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

-- 创建应用用户。root 用于初始化，app_user 供后续应用连接使用。
CREATE USER IF NOT EXISTS 'app_user'@'%' IDENTIFIED BY '123456';
ALTER USER 'app_user'@'%' IDENTIFIED BY '123456';

-- 授权 app_user 读写 dw 和 meta。
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, CREATE VIEW, SHOW VIEW
  ON dw.* TO 'app_user'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, CREATE VIEW, SHOW VIEW
  ON meta.* TO 'app_user'@'%';

FLUSH PRIVILEGES;
