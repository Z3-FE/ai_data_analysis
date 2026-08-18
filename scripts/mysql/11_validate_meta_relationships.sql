-- 11_validate_meta_relationships.sql
-- 作用：校验 Meta 关系数据是否完整。
-- 说明：本脚本只读，不创建、修改或删除任何数据。
-- 使用：在执行 05_build_meta_tables.sql 初始化或更新 Meta 后运行。

USE meta;

-- 检查 relationships 两端字段是否都能在 meta.columns 中定位。
-- 正常情况下本查询不返回任何记录。
SELECT
  r.relationship_id,
  'from' AS endpoint,
  r.from_table_id AS table_id,
  r.from_column_name AS column_name
FROM relationships AS r
LEFT JOIN columns AS c
  ON c.table_id = r.from_table_id
 AND c.column_name = r.from_column_name
WHERE c.column_id IS NULL
UNION ALL
SELECT
  r.relationship_id,
  'to' AS endpoint,
  r.to_table_id AS table_id,
  r.to_column_name AS column_name
FROM relationships AS r
LEFT JOIN columns AS c
  ON c.table_id = r.to_table_id
 AND c.column_name = r.to_column_name
WHERE c.column_id IS NULL;

-- 检查 dimensions 的字段定位是否完整。
-- 正常情况下本查询不返回任何记录。
SELECT
  d.dimension_id,
  d.table_id,
  d.column_name
FROM dimensions AS d
LEFT JOIN columns AS c
  ON c.table_id = d.table_id
 AND c.column_name = d.column_name
WHERE c.column_id IS NULL;

