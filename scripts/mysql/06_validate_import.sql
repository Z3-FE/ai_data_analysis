-- 06_validate_import.sql
-- 作用：
--   导入完成后做基础校验，确认 dw 和 meta 的核心表已经生成，并输出关键指标。
-- 输入：
--   04_build_dw_tables.sql 生成的 dw 表。
--   05_build_meta_tables.sql 生成的 meta 表。
-- 输出：
--   1. 每张核心表的精确行数。
--   2. 销售事实表的销售额、运费、明细数、订单数。
--   3. 订单事实表的订单数、延迟订单数、平均配送天数。
-- 说明：
--   这里使用 COUNT(*)，不是 information_schema.tables.table_rows。
--   InnoDB 的 table_rows 是估算值，不能作为精确校验依据。

-- 校验 DW 和 Meta 核心表行数。
SELECT 'dw.dim_date' AS table_name, COUNT(*) AS row_count FROM dw.dim_date
UNION ALL SELECT 'dw.dim_customer', COUNT(*) FROM dw.dim_customer
UNION ALL SELECT 'dw.dim_product', COUNT(*) FROM dw.dim_product
UNION ALL SELECT 'dw.dim_category', COUNT(*) FROM dw.dim_category
UNION ALL SELECT 'dw.dim_seller', COUNT(*) FROM dw.dim_seller
UNION ALL SELECT 'dw.dim_location', COUNT(*) FROM dw.dim_location
UNION ALL SELECT 'dw.dim_order_status', COUNT(*) FROM dw.dim_order_status
UNION ALL SELECT 'dw.dim_payment_type', COUNT(*) FROM dw.dim_payment_type
UNION ALL SELECT 'dw.fact_order', COUNT(*) FROM dw.fact_order
UNION ALL SELECT 'dw.fact_order_item', COUNT(*) FROM dw.fact_order_item
UNION ALL SELECT 'dw.fact_payment', COUNT(*) FROM dw.fact_payment
UNION ALL SELECT 'dw.fact_review', COUNT(*) FROM dw.fact_review
UNION ALL SELECT 'meta.data_sources', COUNT(*) FROM meta.data_sources
UNION ALL SELECT 'meta.tables', COUNT(*) FROM meta.tables
UNION ALL SELECT 'meta.columns', COUNT(*) FROM meta.columns
UNION ALL SELECT 'meta.relationships', COUNT(*) FROM meta.relationships
UNION ALL SELECT 'meta.metrics', COUNT(*) FROM meta.metrics
UNION ALL SELECT 'meta.dimensions', COUNT(*) FROM meta.dimensions
UNION ALL SELECT 'meta.dimension_values', COUNT(*) FROM meta.dimension_values
UNION ALL SELECT 'meta.metric_dimensions', COUNT(*) FROM meta.metric_dimensions
UNION ALL SELECT 'meta.subject_areas', COUNT(*) FROM meta.subject_areas;

-- 校验销售核心事实：销售额、运费、明细行数、包含明细的订单数。
SELECT
  SUM(price) AS gmv,
  SUM(freight_value) AS freight_amount,
  COUNT(*) AS order_item_rows,
  COUNT(DISTINCT order_id) AS order_count_from_items
FROM dw.fact_order_item;

-- 校验订单履约核心事实：订单数、延迟订单数、平均配送天数。
SELECT
  COUNT(*) AS fact_order_rows,
  SUM(CASE WHEN is_delayed = 1 THEN 1 ELSE 0 END) AS delayed_orders,
  AVG(customer_delivery_days) AS avg_delivery_days
FROM dw.fact_order;
