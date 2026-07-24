-- 03_load_stage_csv.sql
-- 作用：
--   把 Docker 容器里的 Olist CSV 文件导入到 02_create_stage_tables.sql 创建的 stg_* 表。
-- 输入：
--   1. /import-data/raw/olist/*.csv
--      这个路径来自 docker/docker-compose.yml 的只读挂载：
--      ./mysql/data:/import-data:ro
--   2. dw.stg_* 空表。
-- 输出：
--   9 张 stg_* 表被填充原始数据，并补充导入后 join 所需索引。
-- 为什么用 LOAD DATA LOCAL INFILE：
--   MySQL 官方容器会管理 /var/lib/mysql-files 的权限。
--   为了避免只读挂载和容器 chown 冲突，这里让 mysql 客户端从 /import-data 读取文件，
--   再通过 LOCAL INFILE 传给 MySQL 服务端导入。
-- 注意：
--   评价 CSV 中有包含引号的评论文本，因此只有评价表使用 ESCAPED BY '"'。

USE dw;

-- 开启 LOCAL INFILE，允许 mysql 客户端把容器内 CSV 文件传给服务端导入。
SET GLOBAL local_infile = 1;

-- 导入客户 CSV。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_customers_dataset.csv'
INTO TABLE stg_customers
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

-- 导入地理位置 CSV，后续会聚合为 dim_location。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_geolocation_dataset.csv'
INTO TABLE stg_geolocation
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

-- 导入订单 CSV，并把空字符串时间转换成 NULL。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_orders_dataset.csv'
INTO TABLE stg_orders
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(order_id, customer_id, order_status, @purchase_ts, @approved_at, @carrier_date, @customer_date, @estimated_date)
SET
  order_purchase_timestamp = NULLIF(@purchase_ts, ''),
  order_approved_at = NULLIF(@approved_at, ''),
  order_delivered_carrier_date = NULLIF(@carrier_date, ''),
  order_delivered_customer_date = NULLIF(@customer_date, ''),
  order_estimated_delivery_date = NULLIF(@estimated_date, '');

-- 导入订单明细 CSV，并把空字符串发货截止时间转换成 NULL。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_order_items_dataset.csv'
INTO TABLE stg_order_items
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(order_id, order_item_id, product_id, seller_id, @shipping_limit_date, price, freight_value)
SET shipping_limit_date = NULLIF(@shipping_limit_date, '');

-- 导入支付 CSV。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_order_payments_dataset.csv'
INTO TABLE stg_order_payments
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

-- 导入评价 CSV。
-- ESCAPED BY '"' 用来正确解析评价文本里的双引号转义，否则会少导入一条复杂评论记录。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_order_reviews_dataset.csv'
INTO TABLE stg_order_reviews
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"' ESCAPED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(review_id, order_id, review_score, @review_comment_title, @review_comment_message, @review_creation_date, @review_answer_timestamp)
SET
  review_comment_title = NULLIF(@review_comment_title, ''),
  review_comment_message = NULLIF(@review_comment_message, ''),
  review_creation_date = NULLIF(@review_creation_date, ''),
  review_answer_timestamp = NULLIF(@review_answer_timestamp, '');

-- 导入商品 CSV。
-- 源字段 product_name_lenght、product_description_lenght 拼写有误，这里先原样落地。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_products_dataset.csv'
INTO TABLE stg_products
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(product_id, @category_name, @name_length, @description_length, @photos_qty, @weight_g, @length_cm, @height_cm, @width_cm)
SET
  product_category_name = NULLIF(@category_name, ''),
  product_name_lenght = NULLIF(@name_length, ''),
  product_description_lenght = NULLIF(@description_length, ''),
  product_photos_qty = NULLIF(@photos_qty, ''),
  product_weight_g = NULLIF(@weight_g, ''),
  product_length_cm = NULLIF(@length_cm, ''),
  product_height_cm = NULLIF(@height_cm, ''),
  product_width_cm = NULLIF(@width_cm, '');

-- 导入卖家 CSV。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/olist_sellers_dataset.csv'
INTO TABLE stg_sellers
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

-- 导入商品品类翻译 CSV。
LOAD DATA LOCAL INFILE '/import-data/raw/olist/product_category_name_translation.csv'
INTO TABLE stg_product_category_translation
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES;

-- 清理 MySQL 可能读出的零日期。
-- 这类值不能进入 dim_date，否则递归日期生成会失败。
UPDATE stg_order_reviews
SET review_creation_date = NULL
WHERE CAST(review_creation_date AS CHAR) LIKE '0000-00-00%';

UPDATE stg_order_reviews
SET review_answer_timestamp = NULL
WHERE CAST(review_answer_timestamp AS CHAR) LIKE '0000-00-00%';

-- 给 staging 表增加索引。
-- 这些索引不是业务模型的一部分，只是为了让 04_build_dw_tables.sql 的大表 join 更快。
ALTER TABLE stg_customers
  ADD INDEX idx_stg_customers_customer_id (customer_id),
  ADD INDEX idx_stg_customers_unique_id (customer_unique_id),
  ADD INDEX idx_stg_customers_location (customer_zip_code_prefix, customer_city, customer_state);

ALTER TABLE stg_orders
  ADD INDEX idx_stg_orders_order_id (order_id),
  ADD INDEX idx_stg_orders_customer_id (customer_id),
  ADD INDEX idx_stg_orders_status (order_status);

ALTER TABLE stg_order_items
  ADD INDEX idx_stg_order_items_order_id (order_id),
  ADD INDEX idx_stg_order_items_product_id (product_id),
  ADD INDEX idx_stg_order_items_seller_id (seller_id);

ALTER TABLE stg_order_payments
  ADD INDEX idx_stg_order_payments_order_id (order_id),
  ADD INDEX idx_stg_order_payments_type (payment_type);

ALTER TABLE stg_order_reviews
  ADD INDEX idx_stg_order_reviews_order_id (order_id);

ALTER TABLE stg_products
  ADD INDEX idx_stg_products_product_id (product_id),
  ADD INDEX idx_stg_products_category (product_category_name);

ALTER TABLE stg_sellers
  ADD INDEX idx_stg_sellers_seller_id (seller_id),
  ADD INDEX idx_stg_sellers_location (seller_zip_code_prefix, seller_city, seller_state);

ALTER TABLE stg_product_category_translation
  ADD INDEX idx_stg_category_translation_name (product_category_name);
