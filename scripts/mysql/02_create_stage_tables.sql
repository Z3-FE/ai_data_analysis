-- 02_create_stage_tables.sql
-- 作用：
--   在 dw 数据库里创建 stg_* 临时落地表，用来承接 CSV 原始数据。
-- 输入：
--   01_reset_databases.sql 已经创建好的 dw 数据库。
-- 输出：
--   stg_customers、stg_orders、stg_order_items 等 9 张 staging 表。
-- 设计说明：
--   staging 表尽量贴近 CSV 原始字段，只做基础类型约束。
--   后续 04_build_dw_tables.sql 会把这些 staging 表转换成正式的 dim_* 和 fact_*。
-- 注意：
--   stg_* 表只是导入过程中的中间表，最终会在 04_build_dw_tables.sql 末尾删除。

USE dw;

-- 客户源表：一行代表一次订单里的客户身份，同时包含 customer_unique_id 用于识别复购客户。
CREATE TABLE stg_customers (
  customer_id VARCHAR(64),
  customer_unique_id VARCHAR(64),
  customer_zip_code_prefix VARCHAR(16),
  customer_city VARCHAR(128),
  customer_state VARCHAR(8)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始客户 CSV';

-- 地理位置源表：邮编、城市、州、经纬度。原始数据粒度较细，后续会聚合成 dim_location。
CREATE TABLE stg_geolocation (
  geolocation_zip_code_prefix VARCHAR(16),
  geolocation_lat DECIMAL(18, 15),
  geolocation_lng DECIMAL(18, 15),
  geolocation_city VARCHAR(128),
  geolocation_state VARCHAR(8)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始地理位置 CSV';

-- 订单源表：一行一个订单，包含订单状态和订单生命周期时间。
CREATE TABLE stg_orders (
  order_id VARCHAR(64),
  customer_id VARCHAR(64),
  order_status VARCHAR(32),
  order_purchase_timestamp DATETIME NULL,
  order_approved_at DATETIME NULL,
  order_delivered_carrier_date DATETIME NULL,
  order_delivered_customer_date DATETIME NULL,
  order_estimated_delivery_date DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始订单 CSV';

-- 订单明细源表：一行一个订单商品明细，是后续销售事实表的核心来源。
CREATE TABLE stg_order_items (
  order_id VARCHAR(64),
  order_item_id INT,
  product_id VARCHAR(64),
  seller_id VARCHAR(64),
  shipping_limit_date DATETIME NULL,
  price DECIMAL(12, 2),
  freight_value DECIMAL(12, 2)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始订单明细 CSV';

-- 支付源表：一个订单可能有多条支付记录，因此后续单独构建 fact_payment。
CREATE TABLE stg_order_payments (
  order_id VARCHAR(64),
  payment_sequential INT,
  payment_type VARCHAR(32),
  payment_installments INT,
  payment_value DECIMAL(12, 2)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始支付 CSV';

-- 评价源表：评价文本中有逗号、换行和引号，导入时需要特殊 CSV 转义处理。
CREATE TABLE stg_order_reviews (
  review_id VARCHAR(64),
  order_id VARCHAR(64),
  review_score INT,
  review_comment_title TEXT NULL,
  review_comment_message TEXT NULL,
  review_creation_date DATETIME NULL,
  review_answer_timestamp DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始评价 CSV';

-- 商品源表：保留源字段中的 lenght 拼写，正式 dim_product 会修正为 length。
CREATE TABLE stg_products (
  product_id VARCHAR(64),
  product_category_name VARCHAR(128) NULL,
  product_name_lenght INT NULL,
  product_description_lenght INT NULL,
  product_photos_qty INT NULL,
  product_weight_g INT NULL,
  product_length_cm INT NULL,
  product_height_cm INT NULL,
  product_width_cm INT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始商品 CSV';

-- 卖家源表：一行一个卖家，包含卖家的邮编、城市、州。
CREATE TABLE stg_sellers (
  seller_id VARCHAR(64),
  seller_zip_code_prefix VARCHAR(16),
  seller_city VARCHAR(128),
  seller_state VARCHAR(8)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始卖家 CSV';

-- 品类翻译源表：葡萄牙语品类名到英文品类名的映射。
CREATE TABLE stg_product_category_translation (
  product_category_name VARCHAR(128),
  product_category_name_english VARCHAR(128)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='临时表：原始商品品类翻译 CSV';
