-- 09_create_dimension_values.sql
-- 作用：
--   创建面向 AI 检索的维度值目录，并从 DW 的真实数据中汇总第一批可检索值。
-- 输入：
--   dw 中已经构建完成的维度表、事实表，以及 meta.dimensions/meta.columns。
-- 输出：
--   meta.dimension_values。它是 ES 和 Qdrant 字段值索引的结构化数据真源。
-- 说明：
--   raw_value 始终保存 DW 中真实值；display_name 和 aliases 只帮助理解用户表达。
--   月份、评分等结构化数值维度不进入第一版全文索引。

USE meta;

CREATE TABLE dimension_values (
  value_id VARCHAR(512) PRIMARY KEY COMMENT '稳定维度值 ID，格式为 column_id::raw_value',
  dimension_id VARCHAR(128) NOT NULL COMMENT '所属维度 ID，关联 meta.dimensions',
  column_id VARCHAR(192) NOT NULL COMMENT '所属字段 ID，关联 meta.columns',
  raw_value VARCHAR(255) NOT NULL COMMENT 'DW 中真实保存的值，生成 SQL 时必须使用该值',
  normalized_value VARCHAR(255) NOT NULL COMMENT '去除下划线等格式差异后的检索文本',
  display_name VARCHAR(255) NOT NULL COMMENT '标准中文业务名称，未维护时暂用真实值',
  aliases JSON NOT NULL COMMENT '别名、同义词和口语表达',
  description TEXT NOT NULL COMMENT '该维度值的业务说明',
  value_count BIGINT NOT NULL DEFAULT 0 COMMENT '该值在对应 DW 数据中的出现次数',
  semantic_enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否写入 Qdrant 字段值语义索引',
  status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT 'active 启用，inactive 停用',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  UNIQUE KEY uk_dimension_values_column_value (column_id, raw_value),
  KEY idx_dimension_values_dimension (dimension_id),
  KEY idx_dimension_values_status (status),
  CONSTRAINT fk_dimension_values_dimension FOREIGN KEY (dimension_id) REFERENCES dimensions(dimension_id),
  CONSTRAINT fk_dimension_values_column FOREIGN KEY (column_id) REFERENCES columns(column_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='AI 维度值检索目录，是 Elasticsearch 和 Qdrant 字段值索引的数据真源';

-- 商品品类：真实值来自 dim_category，value_count 表示该品类下的商品数量。
INSERT INTO dimension_values (
  value_id, dimension_id, column_id, raw_value, normalized_value,
  display_name, aliases, description, value_count
)
SELECT
  CONCAT('dw.dim_category.category_display_name::', x.raw_value),
  'product_category',
  'dw.dim_category.category_display_name',
  x.raw_value,
  REPLACE(x.raw_value, '_', ' '),
  x.raw_value,
  JSON_ARRAY(),
  CONCAT('商品品类真实值：', x.raw_value),
  x.value_count
FROM (
  SELECT
    TRIM(REPLACE(c.category_display_name, CHAR(13), '')) AS raw_value,
    COUNT(p.product_key) AS value_count
  FROM dw.dim_category AS c
  LEFT JOIN dw.dim_product AS p ON p.category_key = c.category_key
  GROUP BY TRIM(REPLACE(c.category_display_name, CHAR(13), ''))
) AS x;

-- 客户州：raw_value 是巴西州代码，value_count 表示客户数量。
INSERT INTO dimension_values (
  value_id, dimension_id, column_id, raw_value, normalized_value,
  display_name, aliases, description, value_count
)
SELECT
  CONCAT('dw.dim_customer.customer_state::', customer_state),
  'customer_state',
  'dw.dim_customer.customer_state',
  customer_state,
  customer_state,
  customer_state,
  JSON_ARRAY(),
  CONCAT('客户所在巴西州代码：', customer_state),
  COUNT(*)
FROM dw.dim_customer
WHERE customer_state IS NOT NULL
GROUP BY customer_state;

-- 卖家州：与客户州使用相同州代码，但必须保留独立 column_id，避免字段归属混淆。
INSERT INTO dimension_values (
  value_id, dimension_id, column_id, raw_value, normalized_value,
  display_name, aliases, description, value_count
)
SELECT
  CONCAT('dw.dim_seller.seller_state::', seller_state),
  'seller_state',
  'dw.dim_seller.seller_state',
  seller_state,
  seller_state,
  seller_state,
  JSON_ARRAY(),
  CONCAT('卖家所在巴西州代码：', seller_state),
  COUNT(*)
FROM dw.dim_seller
WHERE seller_state IS NOT NULL
GROUP BY seller_state;

-- 订单状态：真实值来自状态维度，value_count 表示订单数量。
INSERT INTO dimension_values (
  value_id, dimension_id, column_id, raw_value, normalized_value,
  display_name, aliases, description, value_count
)
SELECT
  CONCAT('dw.dim_order_status.order_status::', os.order_status),
  'order_status',
  'dw.dim_order_status.order_status',
  os.order_status,
  REPLACE(os.order_status, '_', ' '),
  os.order_status_name,
  JSON_ARRAY(),
  CONCAT('订单状态真实值：', os.order_status),
  COUNT(f.order_id)
FROM dw.dim_order_status AS os
LEFT JOIN dw.fact_order AS f ON f.order_status_key = os.order_status_key
GROUP BY os.order_status, os.order_status_name;

-- 支付方式：真实值来自支付方式维度，value_count 表示支付记录数量。
INSERT INTO dimension_values (
  value_id, dimension_id, column_id, raw_value, normalized_value,
  display_name, aliases, description, value_count
)
SELECT
  CONCAT('dw.dim_payment_type.payment_type::', pt.payment_type),
  'payment_type',
  'dw.dim_payment_type.payment_type',
  pt.payment_type,
  REPLACE(pt.payment_type, '_', ' '),
  pt.payment_type_name,
  JSON_ARRAY(),
  CONCAT('支付方式真实值：', pt.payment_type),
  COUNT(f.order_id)
FROM dw.dim_payment_type AS pt
LEFT JOIN dw.fact_payment AS f ON f.payment_type_key = pt.payment_type_key
GROUP BY pt.payment_type, pt.payment_type_name;

-- 是否延迟：物理类型是 TINYINT，但业务语义是枚举维度，因此需要进入值映射。
INSERT INTO dimension_values (
  value_id, dimension_id, column_id, raw_value, normalized_value,
  display_name, aliases, description, value_count
)
SELECT
  CONCAT('dw.fact_order.is_delayed::', CAST(is_delayed AS CHAR)),
  'is_delayed',
  'dw.fact_order.is_delayed',
  CAST(is_delayed AS CHAR),
  CAST(is_delayed AS CHAR),
  CAST(is_delayed AS CHAR),
  JSON_ARRAY(),
  CONCAT('订单是否延迟送达标记：', CAST(is_delayed AS CHAR)),
  COUNT(*)
FROM dw.fact_order
WHERE is_delayed IS NOT NULL
GROUP BY is_delayed;

