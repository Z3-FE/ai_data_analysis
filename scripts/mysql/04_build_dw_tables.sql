-- 04_build_dw_tables.sql
-- 作用：
--   把 stg_* 原始落地表转换成正式的 DW 分析模型。
-- 输入：
--   dw.stg_* 表，来自 03_load_stage_csv.sql。
-- 输出：
--   1. 维度表 dim_*：日期、客户、商品、品类、卖家、地区、订单状态、支付方式。
--   2. 事实表 fact_*：订单、订单明细、支付、评价。
--   3. 删除 stg_* 中间表，只保留干净的分析表。
-- 建模原则：
--   维度表回答“按什么分析”，事实表回答“分析什么指标”。
--   fact_order_item 是销售分析核心事实表，一行一个 order_id + order_item_id。
--   fact_order 是订单履约事实表，一行一个订单。
--   fact_payment 和 fact_review 独立建表，避免支付和评价的一对多关系污染销售事实。

USE dw;

-- dim_date 需要用递归 CTE 从最小日期生成到最大日期，默认递归深度不够，所以调高。
SET SESSION cte_max_recursion_depth = 5000;

-- 日期维度：统一支撑按天、周、月、季度、年分析。
CREATE TABLE dim_date (
  date_key INT PRIMARY KEY COMMENT '日期代理键，格式为 YYYYMMDD',
  full_date DATE NOT NULL UNIQUE COMMENT '自然日期',
  year_number SMALLINT NOT NULL COMMENT '年份',
  quarter_number TINYINT NOT NULL COMMENT '季度，1-4',
  month_number TINYINT NOT NULL COMMENT '月份，1-12',
  year_month_value CHAR(7) NOT NULL COMMENT '年月，格式 YYYY-MM',
  day_of_month TINYINT NOT NULL COMMENT '月内日期',
  week_of_year TINYINT NOT NULL COMMENT '年内周序号',
  weekday_number TINYINT NOT NULL COMMENT '星期序号，周一为 1，周日为 7',
  weekday_name VARCHAR(16) NOT NULL COMMENT '英文星期名',
  is_weekend TINYINT(1) NOT NULL COMMENT '是否周末'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='日期维度表，统一支持按天、周、月、季度、年分析';

-- 从订单、支付、发货、评价等所有日期字段里找出日期范围，并生成连续日期。
INSERT INTO dim_date (
  date_key,
  full_date,
  year_number,
  quarter_number,
  month_number,
  year_month_value,
  day_of_month,
  week_of_year,
  weekday_number,
  weekday_name,
  is_weekend
)
WITH RECURSIVE
source_dates AS (
  SELECT DATE(order_purchase_timestamp) AS d FROM stg_orders WHERE order_purchase_timestamp IS NOT NULL
  UNION ALL SELECT DATE(order_approved_at) FROM stg_orders WHERE order_approved_at IS NOT NULL
  UNION ALL SELECT DATE(order_delivered_carrier_date) FROM stg_orders WHERE order_delivered_carrier_date IS NOT NULL
  UNION ALL SELECT DATE(order_delivered_customer_date) FROM stg_orders WHERE order_delivered_customer_date IS NOT NULL
  UNION ALL SELECT DATE(order_estimated_delivery_date) FROM stg_orders WHERE order_estimated_delivery_date IS NOT NULL
  UNION ALL SELECT DATE(shipping_limit_date) FROM stg_order_items WHERE shipping_limit_date IS NOT NULL
  UNION ALL SELECT DATE(review_creation_date) FROM stg_order_reviews WHERE review_creation_date IS NOT NULL
  UNION ALL SELECT DATE(review_answer_timestamp) FROM stg_order_reviews WHERE review_answer_timestamp IS NOT NULL
),
bounds AS (
  SELECT MIN(d) AS min_date, MAX(d) AS max_date FROM source_dates
),
dates AS (
  SELECT min_date AS full_date FROM bounds
  UNION ALL
  SELECT DATE_ADD(dates.full_date, INTERVAL 1 DAY)
  FROM dates
  JOIN bounds ON dates.full_date < bounds.max_date
)
SELECT
  CAST(DATE_FORMAT(full_date, '%Y%m%d') AS UNSIGNED),
  full_date,
  YEAR(full_date),
  QUARTER(full_date),
  MONTH(full_date),
  DATE_FORMAT(full_date, '%Y-%m'),
  DAY(full_date),
  WEEKOFYEAR(full_date),
  WEEKDAY(full_date) + 1,
  DAYNAME(full_date),
  CASE WHEN WEEKDAY(full_date) IN (5, 6) THEN 1 ELSE 0 END
FROM dates;

-- 订单状态维度：delivered、shipped、canceled 等。
CREATE TABLE dim_order_status (
  order_status_key INT AUTO_INCREMENT PRIMARY KEY COMMENT '订单状态代理键',
  order_status VARCHAR(32) NOT NULL UNIQUE COMMENT '订单状态编码',
  order_status_name VARCHAR(64) NOT NULL COMMENT '订单状态展示名'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单状态维度表';

INSERT INTO dim_order_status (order_status, order_status_name)
SELECT DISTINCT order_status, order_status
FROM stg_orders
WHERE order_status IS NOT NULL;

-- 支付方式维度：credit_card、boleto、voucher 等。
CREATE TABLE dim_payment_type (
  payment_type_key INT AUTO_INCREMENT PRIMARY KEY COMMENT '支付方式代理键',
  payment_type VARCHAR(32) NOT NULL UNIQUE COMMENT '支付方式编码',
  payment_type_name VARCHAR(64) NOT NULL COMMENT '支付方式展示名'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付方式维度表';

INSERT INTO dim_payment_type (payment_type, payment_type_name)
SELECT DISTINCT payment_type, payment_type
FROM stg_order_payments
WHERE payment_type IS NOT NULL;

CREATE TABLE dim_category (
  category_key INT AUTO_INCREMENT PRIMARY KEY COMMENT '商品品类代理键',
  product_category_name VARCHAR(128) NOT NULL UNIQUE COMMENT '商品品类葡萄牙语名称',
  product_category_name_english VARCHAR(128) NULL COMMENT '商品品类英文名称',
  category_display_name VARCHAR(128) NOT NULL COMMENT '优先英文、缺失时回退到葡萄牙语的展示名称',
  is_translated TINYINT(1) NOT NULL COMMENT '是否存在英文翻译'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品品类维度表';

INSERT INTO dim_category (
  product_category_name,
  product_category_name_english,
  category_display_name,
  is_translated
)
SELECT
  c.product_category_name,
  t.product_category_name_english,
  COALESCE(t.product_category_name_english, c.product_category_name),
  CASE WHEN t.product_category_name_english IS NULL THEN 0 ELSE 1 END
FROM (
  SELECT TRIM(REPLACE(REPLACE(product_category_name, CHAR(13), ''), CHAR(10), '')) AS product_category_name
  FROM stg_products
  WHERE product_category_name IS NOT NULL
  UNION
  SELECT TRIM(REPLACE(REPLACE(product_category_name, CHAR(13), ''), CHAR(10), '')) AS product_category_name
  FROM stg_product_category_translation
  WHERE product_category_name IS NOT NULL
) AS c
LEFT JOIN (
  SELECT
    TRIM(REPLACE(REPLACE(product_category_name, CHAR(13), ''), CHAR(10), '')) AS product_category_name,
    TRIM(REPLACE(REPLACE(product_category_name_english, CHAR(13), ''), CHAR(10), '')) AS product_category_name_english
  FROM stg_product_category_translation
) AS t ON c.product_category_name = t.product_category_name;

CREATE TABLE dim_location (
  location_key INT AUTO_INCREMENT PRIMARY KEY COMMENT '地理位置代理键',
  zip_code_prefix VARCHAR(16) NOT NULL COMMENT '邮编前缀',
  city VARCHAR(128) NOT NULL COMMENT '城市',
  state VARCHAR(8) NOT NULL COMMENT '州',
  latitude DECIMAL(18, 15) NULL COMMENT '平均纬度',
  longitude DECIMAL(18, 15) NULL COMMENT '平均经度',
  source_flags VARCHAR(64) NOT NULL COMMENT '位置来源标记：customer、seller、geolocation',
  UNIQUE KEY uk_dim_location (zip_code_prefix, city, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='地理位置维度表，按邮编、城市、州聚合';

INSERT INTO dim_location (
  zip_code_prefix,
  city,
  state,
  latitude,
  longitude,
  source_flags
)
SELECT
  x.zip_code_prefix,
  x.city,
  x.state,
  AVG(x.latitude),
  AVG(x.longitude),
  GROUP_CONCAT(DISTINCT x.source_name ORDER BY x.source_name SEPARATOR ',')
FROM (
  SELECT customer_zip_code_prefix AS zip_code_prefix, customer_city AS city, customer_state AS state, NULL AS latitude, NULL AS longitude, 'customer' AS source_name
  FROM stg_customers
  UNION ALL
  SELECT seller_zip_code_prefix, seller_city, seller_state, NULL, NULL, 'seller'
  FROM stg_sellers
  UNION ALL
  SELECT geolocation_zip_code_prefix, geolocation_city, geolocation_state, geolocation_lat, geolocation_lng, 'geolocation'
  FROM stg_geolocation
) AS x
WHERE x.zip_code_prefix IS NOT NULL
  AND x.city IS NOT NULL
  AND x.state IS NOT NULL
GROUP BY x.zip_code_prefix, x.city, x.state;

CREATE TABLE dim_customer (
  customer_key INT AUTO_INCREMENT PRIMARY KEY COMMENT '客户代理键',
  customer_id VARCHAR(64) NOT NULL UNIQUE COMMENT '订单级客户 ID',
  customer_unique_id VARCHAR(64) NOT NULL COMMENT '跨订单唯一客户 ID，用于复购分析',
  location_key INT NULL COMMENT '客户地理位置代理键',
  customer_zip_code_prefix VARCHAR(16) COMMENT '客户邮编前缀',
  customer_city VARCHAR(128) COMMENT '客户城市',
  customer_state VARCHAR(8) COMMENT '客户州',
  KEY idx_customer_unique_id (customer_unique_id),
  KEY idx_customer_location_key (location_key),
  CONSTRAINT fk_dim_customer_location FOREIGN KEY (location_key) REFERENCES dim_location(location_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客户维度表';

INSERT INTO dim_customer (
  customer_id,
  customer_unique_id,
  location_key,
  customer_zip_code_prefix,
  customer_city,
  customer_state
)
SELECT
  c.customer_id,
  c.customer_unique_id,
  l.location_key,
  c.customer_zip_code_prefix,
  c.customer_city,
  c.customer_state
FROM stg_customers AS c
LEFT JOIN dim_location AS l
  ON c.customer_zip_code_prefix = l.zip_code_prefix
 AND c.customer_city = l.city
 AND c.customer_state = l.state;

CREATE TABLE dim_seller (
  seller_key INT AUTO_INCREMENT PRIMARY KEY COMMENT '卖家代理键',
  seller_id VARCHAR(64) NOT NULL UNIQUE COMMENT '卖家业务 ID',
  location_key INT NULL COMMENT '卖家地理位置代理键',
  seller_zip_code_prefix VARCHAR(16) COMMENT '卖家邮编前缀',
  seller_city VARCHAR(128) COMMENT '卖家城市',
  seller_state VARCHAR(8) COMMENT '卖家州',
  KEY idx_seller_location_key (location_key),
  CONSTRAINT fk_dim_seller_location FOREIGN KEY (location_key) REFERENCES dim_location(location_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='卖家维度表';

INSERT INTO dim_seller (
  seller_id,
  location_key,
  seller_zip_code_prefix,
  seller_city,
  seller_state
)
SELECT
  s.seller_id,
  l.location_key,
  s.seller_zip_code_prefix,
  s.seller_city,
  s.seller_state
FROM stg_sellers AS s
LEFT JOIN dim_location AS l
  ON s.seller_zip_code_prefix = l.zip_code_prefix
 AND s.seller_city = l.city
 AND s.seller_state = l.state;

CREATE TABLE dim_product (
  product_key INT AUTO_INCREMENT PRIMARY KEY COMMENT '商品代理键',
  product_id VARCHAR(64) NOT NULL UNIQUE COMMENT '商品业务 ID',
  category_key INT NULL COMMENT '商品品类代理键',
  product_category_name VARCHAR(128) NULL COMMENT '商品品类葡萄牙语名称',
  product_category_name_english VARCHAR(128) NULL COMMENT '商品品类英文名称',
  product_name_length INT NULL COMMENT '商品名称长度，源字段 product_name_lenght 已修正拼写',
  product_description_length INT NULL COMMENT '商品描述长度，源字段 product_description_lenght 已修正拼写',
  product_photos_qty INT NULL COMMENT '商品图片数量',
  product_weight_g INT NULL COMMENT '商品重量，克',
  product_length_cm INT NULL COMMENT '商品长度，厘米',
  product_height_cm INT NULL COMMENT '商品高度，厘米',
  product_width_cm INT NULL COMMENT '商品宽度，厘米',
  KEY idx_product_category_key (category_key),
  CONSTRAINT fk_dim_product_category FOREIGN KEY (category_key) REFERENCES dim_category(category_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商品维度表';

INSERT INTO dim_product (
  product_id,
  category_key,
  product_category_name,
  product_category_name_english,
  product_name_length,
  product_description_length,
  product_photos_qty,
  product_weight_g,
  product_length_cm,
  product_height_cm,
  product_width_cm
)
SELECT
  p.product_id,
  c.category_key,
  TRIM(REPLACE(REPLACE(p.product_category_name, CHAR(13), ''), CHAR(10), '')),
  c.product_category_name_english,
  p.product_name_lenght,
  p.product_description_lenght,
  p.product_photos_qty,
  p.product_weight_g,
  p.product_length_cm,
  p.product_height_cm,
  p.product_width_cm
FROM stg_products AS p
LEFT JOIN dim_category AS c
  ON TRIM(REPLACE(REPLACE(p.product_category_name, CHAR(13), ''), CHAR(10), '')) = c.product_category_name;

CREATE TABLE fact_order (
  order_key BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '订单事实代理键',
  order_id VARCHAR(64) NOT NULL UNIQUE COMMENT '订单业务 ID',
  customer_key INT NOT NULL COMMENT '客户代理键',
  order_status_key INT NOT NULL COMMENT '订单状态代理键',
  purchase_date_key INT NOT NULL COMMENT '下单日期键',
  approved_date_key INT NULL COMMENT '审批日期键',
  delivered_carrier_date_key INT NULL COMMENT '交付承运商日期键',
  delivered_customer_date_key INT NULL COMMENT '客户收货日期键',
  estimated_delivery_date_key INT NULL COMMENT '预计送达日期键',
  order_purchase_timestamp DATETIME NOT NULL COMMENT '下单时间',
  order_approved_at DATETIME NULL COMMENT '审批时间',
  order_delivered_carrier_date DATETIME NULL COMMENT '交付承运商时间',
  order_delivered_customer_date DATETIME NULL COMMENT '客户收货时间',
  order_estimated_delivery_date DATETIME NULL COMMENT '预计送达时间',
  order_count INT NOT NULL DEFAULT 1 COMMENT '订单计数指标',
  approval_hours DECIMAL(12, 2) NULL COMMENT '下单到审批小时数',
  carrier_delivery_days DECIMAL(12, 2) NULL COMMENT '审批到交付承运商天数',
  customer_delivery_days DECIMAL(12, 2) NULL COMMENT '下单到客户收货天数',
  delay_days DECIMAL(12, 2) NULL COMMENT '实际送达相对预计送达的延迟天数',
  is_delayed TINYINT(1) NULL COMMENT '是否延迟送达',
  KEY idx_fact_order_customer_key (customer_key),
  KEY idx_fact_order_status_key (order_status_key),
  KEY idx_fact_order_purchase_date_key (purchase_date_key),
  CONSTRAINT fk_fact_order_customer FOREIGN KEY (customer_key) REFERENCES dim_customer(customer_key),
  CONSTRAINT fk_fact_order_status FOREIGN KEY (order_status_key) REFERENCES dim_order_status(order_status_key),
  CONSTRAINT fk_fact_order_purchase_date FOREIGN KEY (purchase_date_key) REFERENCES dim_date(date_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单事实表，一行一个订单，用于订单量、状态和履约分析';

INSERT INTO fact_order (
  order_id,
  customer_key,
  order_status_key,
  purchase_date_key,
  approved_date_key,
  delivered_carrier_date_key,
  delivered_customer_date_key,
  estimated_delivery_date_key,
  order_purchase_timestamp,
  order_approved_at,
  order_delivered_carrier_date,
  order_delivered_customer_date,
  order_estimated_delivery_date,
  approval_hours,
  carrier_delivery_days,
  customer_delivery_days,
  delay_days,
  is_delayed
)
SELECT
  o.order_id,
  c.customer_key,
  os.order_status_key,
  CAST(DATE_FORMAT(o.order_purchase_timestamp, '%Y%m%d') AS UNSIGNED),
  CAST(DATE_FORMAT(o.order_approved_at, '%Y%m%d') AS UNSIGNED),
  CAST(DATE_FORMAT(o.order_delivered_carrier_date, '%Y%m%d') AS UNSIGNED),
  CAST(DATE_FORMAT(o.order_delivered_customer_date, '%Y%m%d') AS UNSIGNED),
  CAST(DATE_FORMAT(o.order_estimated_delivery_date, '%Y%m%d') AS UNSIGNED),
  o.order_purchase_timestamp,
  o.order_approved_at,
  o.order_delivered_carrier_date,
  o.order_delivered_customer_date,
  o.order_estimated_delivery_date,
  TIMESTAMPDIFF(MINUTE, o.order_purchase_timestamp, o.order_approved_at) / 60,
  TIMESTAMPDIFF(HOUR, o.order_approved_at, o.order_delivered_carrier_date) / 24,
  TIMESTAMPDIFF(HOUR, o.order_purchase_timestamp, o.order_delivered_customer_date) / 24,
  TIMESTAMPDIFF(HOUR, o.order_estimated_delivery_date, o.order_delivered_customer_date) / 24,
  CASE
    WHEN o.order_delivered_customer_date IS NULL OR o.order_estimated_delivery_date IS NULL THEN NULL
    WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date THEN 1
    ELSE 0
  END
FROM stg_orders AS o
JOIN dim_customer AS c ON o.customer_id = c.customer_id
JOIN dim_order_status AS os ON o.order_status = os.order_status;

CREATE TABLE fact_order_item (
  order_item_key BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '订单明细事实代理键',
  order_id VARCHAR(64) NOT NULL COMMENT '订单业务 ID',
  order_item_id INT NOT NULL COMMENT '订单内商品明细序号',
  customer_key INT NOT NULL COMMENT '客户代理键',
  product_key INT NOT NULL COMMENT '商品代理键',
  seller_key INT NOT NULL COMMENT '卖家代理键',
  order_status_key INT NOT NULL COMMENT '订单状态代理键',
  purchase_date_key INT NOT NULL COMMENT '下单日期键',
  shipping_limit_date_key INT NULL COMMENT '最晚发货日期键',
  shipping_limit_date DATETIME NULL COMMENT '最晚发货时间',
  price DECIMAL(12, 2) NOT NULL COMMENT '商品成交金额',
  freight_value DECIMAL(12, 2) NOT NULL COMMENT '运费金额',
  item_count INT NOT NULL DEFAULT 1 COMMENT '商品明细计数指标',
  UNIQUE KEY uk_fact_order_item (order_id, order_item_id),
  KEY idx_fact_order_item_product_key (product_key),
  KEY idx_fact_order_item_seller_key (seller_key),
  KEY idx_fact_order_item_customer_key (customer_key),
  KEY idx_fact_order_item_purchase_date_key (purchase_date_key),
  CONSTRAINT fk_fact_order_item_customer FOREIGN KEY (customer_key) REFERENCES dim_customer(customer_key),
  CONSTRAINT fk_fact_order_item_product FOREIGN KEY (product_key) REFERENCES dim_product(product_key),
  CONSTRAINT fk_fact_order_item_seller FOREIGN KEY (seller_key) REFERENCES dim_seller(seller_key),
  CONSTRAINT fk_fact_order_item_status FOREIGN KEY (order_status_key) REFERENCES dim_order_status(order_status_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='订单明细事实表，一行一个订单商品明细，用于销售、商品、品类和卖家分析';

INSERT INTO fact_order_item (
  order_id,
  order_item_id,
  customer_key,
  product_key,
  seller_key,
  order_status_key,
  purchase_date_key,
  shipping_limit_date_key,
  shipping_limit_date,
  price,
  freight_value
)
SELECT
  oi.order_id,
  oi.order_item_id,
  c.customer_key,
  p.product_key,
  s.seller_key,
  os.order_status_key,
  CAST(DATE_FORMAT(o.order_purchase_timestamp, '%Y%m%d') AS UNSIGNED),
  CAST(DATE_FORMAT(oi.shipping_limit_date, '%Y%m%d') AS UNSIGNED),
  oi.shipping_limit_date,
  oi.price,
  oi.freight_value
FROM stg_order_items AS oi
JOIN stg_orders AS o ON oi.order_id = o.order_id
JOIN dim_customer AS c ON o.customer_id = c.customer_id
JOIN dim_product AS p ON oi.product_id = p.product_id
JOIN dim_seller AS s ON oi.seller_id = s.seller_id
JOIN dim_order_status AS os ON o.order_status = os.order_status;

CREATE TABLE fact_payment (
  payment_key BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '支付事实代理键',
  order_id VARCHAR(64) NOT NULL COMMENT '订单业务 ID',
  payment_sequential INT NOT NULL COMMENT '订单内支付序号',
  customer_key INT NOT NULL COMMENT '客户代理键',
  payment_type_key INT NOT NULL COMMENT '支付方式代理键',
  purchase_date_key INT NOT NULL COMMENT '下单日期键',
  payment_installments INT NOT NULL COMMENT '支付分期期数',
  payment_value DECIMAL(12, 2) NOT NULL COMMENT '支付金额',
  payment_count INT NOT NULL DEFAULT 1 COMMENT '支付记录计数指标',
  UNIQUE KEY uk_fact_payment (order_id, payment_sequential),
  KEY idx_fact_payment_type_key (payment_type_key),
  KEY idx_fact_payment_customer_key (customer_key),
  KEY idx_fact_payment_purchase_date_key (purchase_date_key),
  CONSTRAINT fk_fact_payment_customer FOREIGN KEY (customer_key) REFERENCES dim_customer(customer_key),
  CONSTRAINT fk_fact_payment_type FOREIGN KEY (payment_type_key) REFERENCES dim_payment_type(payment_type_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付事实表，一行一条支付记录，用于支付方式、分期和支付金额分析';

INSERT INTO fact_payment (
  order_id,
  payment_sequential,
  customer_key,
  payment_type_key,
  purchase_date_key,
  payment_installments,
  payment_value
)
SELECT
  p.order_id,
  p.payment_sequential,
  c.customer_key,
  pt.payment_type_key,
  CAST(DATE_FORMAT(o.order_purchase_timestamp, '%Y%m%d') AS UNSIGNED),
  p.payment_installments,
  p.payment_value
FROM stg_order_payments AS p
JOIN stg_orders AS o ON p.order_id = o.order_id
JOIN dim_customer AS c ON o.customer_id = c.customer_id
JOIN dim_payment_type AS pt ON p.payment_type = pt.payment_type;

CREATE TABLE fact_review (
  review_key BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '评价事实代理键',
  review_id VARCHAR(64) NOT NULL COMMENT '评价业务 ID',
  order_id VARCHAR(64) NOT NULL COMMENT '订单业务 ID',
  customer_key INT NOT NULL COMMENT '客户代理键',
  review_creation_date_key INT NULL COMMENT '评价创建日期键',
  review_answer_date_key INT NULL COMMENT '评价回复日期键',
  review_score INT NOT NULL COMMENT '评价分数，1-5',
  has_comment TINYINT(1) NOT NULL COMMENT '是否有评价正文',
  comment_title_length INT NULL COMMENT '评价标题长度',
  comment_message_length INT NULL COMMENT '评价正文长度',
  review_response_hours DECIMAL(12, 2) NULL COMMENT '评价创建到商家回复小时数',
  KEY idx_fact_review_order_id (order_id),
  KEY idx_fact_review_customer_key (customer_key),
  KEY idx_fact_review_creation_date_key (review_creation_date_key),
  CONSTRAINT fk_fact_review_customer FOREIGN KEY (customer_key) REFERENCES dim_customer(customer_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='评价事实表，一行一条评价记录，用于评分、好评率、差评和评论分析';

INSERT INTO fact_review (
  review_id,
  order_id,
  customer_key,
  review_creation_date_key,
  review_answer_date_key,
  review_score,
  has_comment,
  comment_title_length,
  comment_message_length,
  review_response_hours
)
SELECT
  r.review_id,
  r.order_id,
  c.customer_key,
  CAST(DATE_FORMAT(r.review_creation_date, '%Y%m%d') AS UNSIGNED),
  CAST(DATE_FORMAT(r.review_answer_timestamp, '%Y%m%d') AS UNSIGNED),
  r.review_score,
  CASE WHEN r.review_comment_message IS NULL THEN 0 ELSE 1 END,
  CHAR_LENGTH(r.review_comment_title),
  CHAR_LENGTH(r.review_comment_message),
  TIMESTAMPDIFF(MINUTE, r.review_creation_date, r.review_answer_timestamp) / 60
FROM stg_order_reviews AS r
JOIN stg_orders AS o ON r.order_id = o.order_id
JOIN dim_customer AS c ON o.customer_id = c.customer_id;

DROP TABLE stg_product_category_translation;
DROP TABLE stg_sellers;
DROP TABLE stg_products;
DROP TABLE stg_order_reviews;
DROP TABLE stg_order_payments;
DROP TABLE stg_order_items;
DROP TABLE stg_orders;
DROP TABLE stg_geolocation;
DROP TABLE stg_customers;
