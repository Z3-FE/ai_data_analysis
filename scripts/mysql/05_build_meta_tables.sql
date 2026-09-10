-- 05_build_meta_tables.sql
-- 作用：
--   构建 meta 数据库，也就是 AI 智能分析平台的语义层说明书。
-- 输入：
--   04_build_dw_tables.sql 已经创建好的 dw.dim_* 和 dw.fact_*。
-- 输出：
--   meta 中的表元数据、字段元数据、表关系、指标、维度、主题域、业务术语、示例 SQL。
-- 为什么需要 meta：
--   AI 不应该直接猜 SQL。
--   它应该先理解“有哪些表、表的粒度是什么、指标怎么算、维度在哪里、哪些指标能按哪些维度拆”，
--   再基于这些结构化元数据生成查询 dw 的 SQL。

USE meta;

-- 数据源注册表：声明当前可分析的数据源是 dw。
CREATE TABLE data_sources (
  data_source_id VARCHAR(64) PRIMARY KEY COMMENT '数据源 ID',
  data_source_name VARCHAR(128) NOT NULL COMMENT '数据源名称',
  database_name VARCHAR(64) NOT NULL COMMENT 'MySQL 数据库名',
  dialect VARCHAR(32) NOT NULL COMMENT 'SQL 方言',
  description TEXT NOT NULL COMMENT '数据源说明'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='数据源注册表，告诉 AI 可以查询哪些业务数据源';

-- 表元数据：记录每张 DW 表是维度表还是事实表，以及它的粒度。
CREATE TABLE tables (
  table_id VARCHAR(128) PRIMARY KEY COMMENT '表 ID',
  data_source_id VARCHAR(64) NOT NULL COMMENT '数据源 ID',
  database_name VARCHAR(64) NOT NULL COMMENT '数据库名',
  table_name VARCHAR(128) NOT NULL COMMENT '物理表名',
  table_type ENUM('dimension', 'fact') NOT NULL COMMENT '表类型：维度表或事实表',
  business_name VARCHAR(128) NOT NULL COMMENT '业务名称',
  grain TEXT NOT NULL COMMENT '表粒度说明',
  description TEXT NOT NULL COMMENT '表业务说明',
  aliases JSON NULL COMMENT '表的别名、同义词、业务叫法，用于向量检索召回增强',
  status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT '元数据状态，active 表示启用，inactive 表示停用',
  UNIQUE KEY uk_meta_tables (database_name, table_name),
  CONSTRAINT fk_meta_tables_data_source FOREIGN KEY (data_source_id) REFERENCES data_sources(data_source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='DW 表元数据，描述每张表的类型、粒度和用途';

-- 字段元数据：记录字段中文名、语义角色、是否可查询、是否可聚合。
CREATE TABLE columns (
  column_id VARCHAR(192) PRIMARY KEY COMMENT '字段 ID',
  table_id VARCHAR(128) NOT NULL COMMENT '表 ID',
  column_name VARCHAR(128) NOT NULL COMMENT '物理字段名',
  business_name VARCHAR(128) NOT NULL COMMENT '业务字段名',
  data_type VARCHAR(64) NOT NULL COMMENT '字段类型',
  semantic_role ENUM('primary_key', 'foreign_key', 'dimension', 'measure', 'timestamp', 'attribute') NOT NULL COMMENT '字段语义角色',
  is_queryable TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否允许 AI 查询',
  is_aggregatable TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否允许聚合',
  description TEXT NOT NULL COMMENT '字段业务说明',
  aliases JSON NULL COMMENT '字段的别名、同义词、业务叫法，用于向量检索召回增强',
  status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT '元数据状态，active 表示启用，inactive 表示停用',
  UNIQUE KEY uk_meta_columns (table_id, column_name),
  CONSTRAINT fk_meta_columns_table FOREIGN KEY (table_id) REFERENCES tables(table_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='字段元数据，描述字段含义、角色和可查询能力';

-- 表关系元数据：记录事实表如何 join 维度表。
CREATE TABLE relationships (
  relationship_id VARCHAR(192) PRIMARY KEY COMMENT '关系 ID',
  from_table_id VARCHAR(128) NOT NULL COMMENT '事实表或明细侧表 ID',
  from_column_name VARCHAR(128) NOT NULL COMMENT '事实表外键字段',
  to_table_id VARCHAR(128) NOT NULL COMMENT '维度表 ID',
  to_column_name VARCHAR(128) NOT NULL COMMENT '维度表主键字段',
  relationship_type VARCHAR(32) NOT NULL COMMENT '关系类型，通常为 many_to_one',
  description TEXT NOT NULL COMMENT '关系说明',
  CONSTRAINT fk_meta_relationship_from_table FOREIGN KEY (from_table_id) REFERENCES tables(table_id),
  CONSTRAINT fk_meta_relationship_to_table FOREIGN KEY (to_table_id) REFERENCES tables(table_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='表关系元数据，告诉 AI 事实表如何关联维度表';

-- 指标定义：记录销售额、订单量、客单价等指标的计算表达式和业务口径。
CREATE TABLE metrics (
  metric_id VARCHAR(128) PRIMARY KEY COMMENT '指标 ID',
  metric_name VARCHAR(128) NOT NULL COMMENT '指标英文名',
  business_name VARCHAR(128) NOT NULL COMMENT '指标中文名',
  base_table_id VARCHAR(128) NOT NULL COMMENT '指标默认事实表',
  expression_sql TEXT NOT NULL COMMENT '指标 SQL 表达式',
  aggregation_type VARCHAR(32) NOT NULL COMMENT '聚合类型',
  calculation_grain TEXT NOT NULL COMMENT '指标计算粒度，说明指标每一行或每个计算集合代表什么',
  aggregation_rule TEXT NOT NULL COMMENT '指标聚合规则和跨粒度使用限制',
  unit VARCHAR(32) NULL COMMENT '指标单位',
  description TEXT NOT NULL COMMENT '指标业务口径',
  aliases JSON NULL COMMENT '指标的别名、同义词、业务叫法，用于向量检索召回增强',
  status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT '元数据状态，active 表示启用，inactive 表示停用',
  CONSTRAINT fk_meta_metrics_base_table FOREIGN KEY (base_table_id) REFERENCES tables(table_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指标定义表，记录销售额、订单量、客单价等指标口径';

-- 维度定义：记录月份、品类、客户州、支付方式等分析角度来自哪里。
CREATE TABLE dimensions (
  dimension_id VARCHAR(128) PRIMARY KEY COMMENT '维度 ID',
  dimension_name VARCHAR(128) NOT NULL COMMENT '维度英文名',
  business_name VARCHAR(128) NOT NULL COMMENT '维度中文名',
  table_id VARCHAR(128) NOT NULL COMMENT '维度所在表 ID',
  column_name VARCHAR(128) NOT NULL COMMENT '维度字段名',
  description TEXT NOT NULL COMMENT '维度业务说明',
  CONSTRAINT fk_meta_dimensions_table FOREIGN KEY (table_id) REFERENCES tables(table_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='维度定义表，记录日期、品类、客户地区、卖家等分析角度';

-- 指标与维度分析口径：记录指标按维度分析时是否支持，以及必要的使用限制。
CREATE TABLE metric_dimensions (
  metric_id VARCHAR(128) NOT NULL COMMENT '指标 ID',
  dimension_id VARCHAR(128) NOT NULL COMMENT '维度 ID',
  compatibility_note TEXT NOT NULL COMMENT '兼容说明',
  support_level ENUM('supported', 'conditional', 'unsupported') NOT NULL DEFAULT 'supported' COMMENT '支持级别：支持、有条件支持或不支持',
  usage_note TEXT NOT NULL COMMENT '按该维度分析时的聚合、关联和粒度说明',
  PRIMARY KEY (metric_id, dimension_id),
  CONSTRAINT fk_meta_metric_dimensions_metric FOREIGN KEY (metric_id) REFERENCES metrics(metric_id),
  CONSTRAINT fk_meta_metric_dimensions_dimension FOREIGN KEY (dimension_id) REFERENCES dimensions(dimension_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指标与维度兼容关系表，限制 AI 合法组合指标和维度';

CREATE TABLE subject_areas (
  subject_area_id VARCHAR(128) PRIMARY KEY COMMENT '主题域 ID',
  subject_area_name VARCHAR(128) NOT NULL COMMENT '主题域名称',
  description TEXT NOT NULL COMMENT '主题域说明'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分析主题域表，例如销售分析、订单分析、支付分析';

CREATE TABLE subject_area_metrics (
  subject_area_id VARCHAR(128) NOT NULL COMMENT '主题域 ID',
  metric_id VARCHAR(128) NOT NULL COMMENT '指标 ID',
  PRIMARY KEY (subject_area_id, metric_id),
  CONSTRAINT fk_meta_subject_area_metrics_area FOREIGN KEY (subject_area_id) REFERENCES subject_areas(subject_area_id),
  CONSTRAINT fk_meta_subject_area_metrics_metric FOREIGN KEY (metric_id) REFERENCES metrics(metric_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='主题域与指标关系表';

CREATE TABLE subject_area_dimensions (
  subject_area_id VARCHAR(128) NOT NULL COMMENT '主题域 ID',
  dimension_id VARCHAR(128) NOT NULL COMMENT '维度 ID',
  PRIMARY KEY (subject_area_id, dimension_id),
  CONSTRAINT fk_meta_subject_area_dimensions_area FOREIGN KEY (subject_area_id) REFERENCES subject_areas(subject_area_id),
  CONSTRAINT fk_meta_subject_area_dimensions_dimension FOREIGN KEY (dimension_id) REFERENCES dimensions(dimension_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='主题域与维度关系表';

CREATE TABLE business_terms (
  term_id VARCHAR(128) PRIMARY KEY COMMENT '术语 ID',
  term_name VARCHAR(128) NOT NULL COMMENT '术语名称',
  definition TEXT NOT NULL COMMENT '术语定义',
  related_metric_id VARCHAR(128) NULL COMMENT '关联指标 ID',
  CONSTRAINT fk_meta_business_terms_metric FOREIGN KEY (related_metric_id) REFERENCES metrics(metric_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务术语表，用于沉淀 GMV、客单价、延迟订单等口径解释';

CREATE TABLE query_examples (
  example_id VARCHAR(128) PRIMARY KEY COMMENT '示例 ID',
  question TEXT NOT NULL COMMENT '自然语言问题',
  sql_text TEXT NOT NULL COMMENT '对应 SQL 示例',
  explanation TEXT NOT NULL COMMENT '示例说明'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='高质量示例问题和 SQL，用于提升 AI 生成 SQL 的稳定性';

INSERT INTO data_sources VALUES
('olist_dw', 'Olist DW', 'dw', 'mysql', 'Olist 电商数据的分析型数据仓库，包含维度表和事实表。');

INSERT INTO tables (
  table_id, data_source_id, database_name, table_name, table_type,
  business_name, grain, description
) VALUES
('dw.dim_date', 'olist_dw', 'dw', 'dim_date', 'dimension', '日期维度', '一个自然日期一行', '支持按天、周、月、季度、年分析。'),
('dw.dim_customer', 'olist_dw', 'dw', 'dim_customer', 'dimension', '客户维度', '一个 customer_id 一行', '保存订单级客户 ID、唯一客户 ID 和客户地区。'),
('dw.dim_product', 'olist_dw', 'dw', 'dim_product', 'dimension', '商品维度', '一个 product_id 一行', '保存商品、品类、图片、重量和尺寸属性。'),
('dw.dim_category', 'olist_dw', 'dw', 'dim_category', 'dimension', '商品品类维度', '一个商品品类一行', '保存葡萄牙语品类名、英文品类名和展示名称。'),
('dw.dim_seller', 'olist_dw', 'dw', 'dim_seller', 'dimension', '卖家维度', '一个 seller_id 一行', '保存卖家 ID 和卖家地区。'),
('dw.dim_location', 'olist_dw', 'dw', 'dim_location', 'dimension', '地理位置维度', '一个邮编、城市、州组合一行', '支持客户和卖家的区域分析。'),
('dw.dim_order_status', 'olist_dw', 'dw', 'dim_order_status', 'dimension', '订单状态维度', '一个订单状态一行', '保存 delivered、shipped、canceled 等订单状态。'),
('dw.dim_payment_type', 'olist_dw', 'dw', 'dim_payment_type', 'dimension', '支付方式维度', '一个支付方式一行', '保存 credit_card、boleto、voucher 等支付方式。'),
('dw.fact_order', 'olist_dw', 'dw', 'fact_order', 'fact', '订单事实', '一个订单一行', '用于订单量、状态、履约和物流时效分析。'),
('dw.fact_order_item', 'olist_dw', 'dw', 'fact_order_item', 'fact', '订单明细事实', '一个 order_id + order_item_id 一行', '用于销售额、商品销量、品类销售和卖家销售分析。'),
('dw.fact_payment', 'olist_dw', 'dw', 'fact_payment', 'fact', '支付事实', '一个订单支付记录一行', '用于支付方式、分期和支付金额分析。'),
('dw.fact_review', 'olist_dw', 'dw', 'fact_review', 'fact', '评价事实', '一条评价记录一行', '用于评分、好评率、差评和评论分析。');

INSERT INTO columns (
  column_id, table_id, column_name, business_name, data_type,
  semantic_role, is_queryable, is_aggregatable, description
) VALUES
('dw.dim_customer.customer_key', 'dw.dim_customer', 'customer_key', '客户代理键', 'INT', 'primary_key', 1, 0, '客户维度表的代理主键，用于事实表关联。'),
('dw.dim_customer.location_key', 'dw.dim_customer', 'location_key', '客户地理位置代理键', 'INT', 'foreign_key', 1, 0, '客户对应地理位置维度的代理键。'),
('dw.dim_date.date_key', 'dw.dim_date', 'date_key', '日期代理键', 'INT', 'primary_key', 1, 0, '日期维度表的代理主键，格式为 YYYYMMDD。'),
('dw.dim_category.category_key', 'dw.dim_category', 'category_key', '商品品类代理键', 'INT', 'primary_key', 1, 0, '商品品类维度表的代理主键。'),
('dw.dim_location.location_key', 'dw.dim_location', 'location_key', '地理位置代理键', 'INT', 'primary_key', 1, 0, '地理位置维度表的代理主键。'),
('dw.dim_product.product_key', 'dw.dim_product', 'product_key', '商品代理键', 'INT', 'primary_key', 1, 0, '商品维度表的代理主键，用于订单明细关联。'),
('dw.dim_product.product_id', 'dw.dim_product', 'product_id', '商品业务 ID', 'VARCHAR(64)', 'attribute', 1, 0, '商品的业务唯一标识。'),
('dw.dim_product.category_key', 'dw.dim_product', 'category_key', '商品品类代理键', 'INT', 'foreign_key', 1, 0, '商品所属品类的代理键。'),
('dw.dim_product.product_category_name', 'dw.dim_product', 'product_category_name', '商品品类葡萄牙语名称', 'VARCHAR(128)', 'dimension', 1, 0, '商品所属品类的葡萄牙语名称。'),
('dw.dim_product.product_category_name_english', 'dw.dim_product', 'product_category_name_english', '商品品类英文名称', 'VARCHAR(128)', 'dimension', 1, 0, '商品所属品类的英文名称。'),
('dw.dim_product.product_name_length', 'dw.dim_product', 'product_name_length', '商品名称长度', 'INT', 'measure', 1, 1, '商品名称的字符长度。'),
('dw.dim_product.product_description_length', 'dw.dim_product', 'product_description_length', '商品描述长度', 'INT', 'measure', 1, 1, '商品描述的字符长度。'),
('dw.dim_product.product_photos_qty', 'dw.dim_product', 'product_photos_qty', '商品图片数量', 'INT', 'measure', 1, 1, '商品关联的图片数量。'),
('dw.dim_product.product_weight_g', 'dw.dim_product', 'product_weight_g', '商品重量', 'INT', 'measure', 1, 1, '商品重量，单位为克。'),
('dw.dim_product.product_length_cm', 'dw.dim_product', 'product_length_cm', '商品长度', 'INT', 'measure', 1, 1, '商品长度，单位为厘米。'),
('dw.dim_product.product_height_cm', 'dw.dim_product', 'product_height_cm', '商品高度', 'INT', 'measure', 1, 1, '商品高度，单位为厘米。'),
('dw.dim_product.product_width_cm', 'dw.dim_product', 'product_width_cm', '商品宽度', 'INT', 'measure', 1, 1, '商品宽度，单位为厘米。'),
('dw.dim_seller.seller_key', 'dw.dim_seller', 'seller_key', '卖家代理键', 'INT', 'primary_key', 1, 0, '卖家维度表的代理主键，用于事实表关联。'),
('dw.dim_seller.location_key', 'dw.dim_seller', 'location_key', '卖家地理位置代理键', 'INT', 'foreign_key', 1, 0, '卖家对应地理位置维度的代理键。'),
('dw.dim_order_status.order_status_key', 'dw.dim_order_status', 'order_status_key', '订单状态代理键', 'INT', 'primary_key', 1, 0, '订单状态维度表的代理主键，用于订单事实关联。'),
('dw.dim_payment_type.payment_type_key', 'dw.dim_payment_type', 'payment_type_key', '支付方式代理键', 'INT', 'primary_key', 1, 0, '支付方式维度表的代理主键，用于支付事实关联。'),
('dw.fact_order.customer_key', 'dw.fact_order', 'customer_key', '客户代理键', 'INT', 'foreign_key', 1, 0, '订单对应客户维度的代理键。'),
('dw.fact_order.order_id', 'dw.fact_order', 'order_id', '订单业务 ID', 'VARCHAR(64)', 'attribute', 1, 0, '订单的业务唯一标识，用于跨事实表筛选。'),
('dw.fact_order.purchase_date_key', 'dw.fact_order', 'purchase_date_key', '下单日期键', 'INT', 'foreign_key', 1, 0, '订单事实对应下单日期维度的代理键。'),
('dw.fact_order.approved_date_key', 'dw.fact_order', 'approved_date_key', '审批日期键', 'INT', 'foreign_key', 1, 0, '订单审批日期对应日期维度的代理键。'),
('dw.fact_order.delivered_carrier_date_key', 'dw.fact_order', 'delivered_carrier_date_key', '交付承运商日期键', 'INT', 'foreign_key', 1, 0, '订单交付承运商日期对应日期维度的代理键。'),
('dw.fact_order.delivered_customer_date_key', 'dw.fact_order', 'delivered_customer_date_key', '客户收货日期键', 'INT', 'foreign_key', 1, 0, '客户实际收货日期对应日期维度的代理键。'),
('dw.fact_order.estimated_delivery_date_key', 'dw.fact_order', 'estimated_delivery_date_key', '预计送达日期键', 'INT', 'foreign_key', 1, 0, '订单预计送达日期对应日期维度的代理键。'),
('dw.fact_order.order_status_key', 'dw.fact_order', 'order_status_key', '订单状态代理键', 'INT', 'foreign_key', 1, 0, '订单对应订单状态维度的代理键。'),
('dw.fact_order_item.customer_key', 'dw.fact_order_item', 'customer_key', '客户代理键', 'INT', 'foreign_key', 1, 0, '订单明细对应客户维度的代理键。'),
('dw.fact_order_item.order_id', 'dw.fact_order_item', 'order_id', '订单业务 ID', 'VARCHAR(64)', 'attribute', 1, 0, '订单明细所属订单的业务标识，用于跨事实表过滤。'),
('dw.fact_order_item.purchase_date_key', 'dw.fact_order_item', 'purchase_date_key', '下单日期键', 'INT', 'foreign_key', 1, 0, '订单明细对应下单日期维度的代理键。'),
('dw.fact_order_item.shipping_limit_date_key', 'dw.fact_order_item', 'shipping_limit_date_key', '最晚发货日期键', 'INT', 'foreign_key', 1, 0, '订单明细最晚发货日期对应日期维度的代理键。'),
('dw.fact_order_item.product_key', 'dw.fact_order_item', 'product_key', '商品代理键', 'INT', 'foreign_key', 1, 0, '订单明细对应商品维度的代理键。'),
('dw.fact_order_item.seller_key', 'dw.fact_order_item', 'seller_key', '卖家代理键', 'INT', 'foreign_key', 1, 0, '订单明细对应卖家维度的代理键。'),
('dw.fact_order_item.order_status_key', 'dw.fact_order_item', 'order_status_key', '订单状态代理键', 'INT', 'foreign_key', 1, 0, '订单明细对应订单状态维度的代理键。'),
('dw.fact_payment.customer_key', 'dw.fact_payment', 'customer_key', '客户代理键', 'INT', 'foreign_key', 1, 0, '支付记录对应客户维度的代理键。'),
('dw.fact_payment.order_id', 'dw.fact_payment', 'order_id', '订单业务 ID', 'VARCHAR(64)', 'attribute', 1, 0, '支付记录所属订单的业务标识，用于筛选满足支付条件的订单。'),
('dw.fact_payment.purchase_date_key', 'dw.fact_payment', 'purchase_date_key', '下单日期键', 'INT', 'foreign_key', 1, 0, '支付记录所属订单的下单日期键。'),
('dw.fact_payment.payment_type_key', 'dw.fact_payment', 'payment_type_key', '支付方式代理键', 'INT', 'foreign_key', 1, 0, '支付记录对应支付方式维度的代理键。'),
('dw.fact_review.customer_key', 'dw.fact_review', 'customer_key', '客户代理键', 'INT', 'foreign_key', 1, 0, '评价记录对应客户维度的代理键。'),
('dw.fact_review.order_id', 'dw.fact_review', 'order_id', '订单业务 ID', 'VARCHAR(64)', 'attribute', 1, 0, '评价所属订单的业务标识，用于跨事实表筛选。'),
('dw.fact_review.review_creation_date_key', 'dw.fact_review', 'review_creation_date_key', '评价创建日期键', 'INT', 'foreign_key', 1, 0, '评价创建日期对应日期维度的代理键。'),
('dw.fact_review.review_answer_date_key', 'dw.fact_review', 'review_answer_date_key', '评价回复日期键', 'INT', 'foreign_key', 1, 0, '评价回复日期对应日期维度的代理键。'),
('dw.fact_order_item.price', 'dw.fact_order_item', 'price', '商品成交金额', 'DECIMAL(12,2)', 'measure', 1, 1, '商品明细成交金额，不含运费。'),
('dw.fact_order_item.freight_value', 'dw.fact_order_item', 'freight_value', '运费金额', 'DECIMAL(12,2)', 'measure', 1, 1, '订单明细对应的运费金额。'),
('dw.fact_order_item.item_count', 'dw.fact_order_item', 'item_count', '商品明细数', 'INT', 'measure', 1, 1, '每行固定为 1，用于统计商品明细数。'),
('dw.fact_order.order_count', 'dw.fact_order', 'order_count', '订单数', 'INT', 'measure', 1, 1, '每行固定为 1，用于统计订单数。'),
('dw.fact_order.customer_delivery_days', 'dw.fact_order', 'customer_delivery_days', '客户配送天数', 'DECIMAL(12,2)', 'measure', 1, 1, '下单到客户收货的天数。'),
('dw.fact_order.is_delayed', 'dw.fact_order', 'is_delayed', '是否延迟', 'TINYINT', 'dimension', 1, 0, '实际送达晚于预计送达时为 1。'),
('dw.fact_payment.payment_value', 'dw.fact_payment', 'payment_value', '支付金额', 'DECIMAL(12,2)', 'measure', 1, 1, '支付记录金额。'),
('dw.fact_review.review_score', 'dw.fact_review', 'review_score', '评价分数', 'INT', 'measure', 1, 1, '客户评价分数，范围 1-5。'),
('dw.dim_date.year_month_value', 'dw.dim_date', 'year_month_value', '年月', 'CHAR(7)', 'dimension', 1, 0, '格式 YYYY-MM。'),
('dw.dim_category.category_display_name', 'dw.dim_category', 'category_display_name', '商品品类', 'VARCHAR(128)', 'dimension', 1, 0, '优先英文名，缺失时使用葡萄牙语品类名。'),
('dw.dim_customer.customer_state', 'dw.dim_customer', 'customer_state', '客户州', 'VARCHAR(8)', 'dimension', 1, 0, '客户所在州。'),
('dw.dim_seller.seller_state', 'dw.dim_seller', 'seller_state', '卖家州', 'VARCHAR(8)', 'dimension', 1, 0, '卖家所在州。'),
('dw.dim_order_status.order_status', 'dw.dim_order_status', 'order_status', '订单状态', 'VARCHAR(32)', 'dimension', 1, 0, '订单状态。'),
('dw.dim_payment_type.payment_type', 'dw.dim_payment_type', 'payment_type', '支付方式', 'VARCHAR(32)', 'dimension', 1, 0, '支付方式。');

INSERT INTO relationships VALUES
('dim_customer_to_dim_location', 'dw.dim_customer', 'location_key', 'dw.dim_location', 'location_key', 'many_to_one', '客户维度关联地理位置维度。'),
('dim_product_to_dim_category', 'dw.dim_product', 'category_key', 'dw.dim_category', 'category_key', 'many_to_one', '商品维度关联商品品类维度。'),
('dim_seller_to_dim_location', 'dw.dim_seller', 'location_key', 'dw.dim_location', 'location_key', 'many_to_one', '卖家维度关联地理位置维度。'),
('fact_order_item_to_dim_customer', 'dw.fact_order_item', 'customer_key', 'dw.dim_customer', 'customer_key', 'many_to_one', '订单明细事实关联客户维度。'),
('fact_order_item_to_dim_date', 'dw.fact_order_item', 'purchase_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '订单明细事实按下单日期关联日期维度。'),
('fact_order_item_to_dim_shipping_limit_date', 'dw.fact_order_item', 'shipping_limit_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '订单明细事实按最晚发货日期关联日期维度。'),
('fact_order_item_to_fact_payment_by_order', 'dw.fact_order_item', 'order_id', 'dw.fact_payment', 'order_id', 'filter_exists', '按支付条件筛选销售明细时使用 EXISTS 关联订单，禁止直接 JOIN 后聚合，以免商品明细与多条支付记录相乘。'),
('fact_order_item_to_fact_review_by_order', 'dw.fact_order_item', 'order_id', 'dw.fact_review', 'order_id', 'filter_exists', '按评价条件筛选销售明细时使用 EXISTS 关联订单，避免评价与商品明细相乘。'),
('fact_order_item_to_dim_product', 'dw.fact_order_item', 'product_key', 'dw.dim_product', 'product_key', 'many_to_one', '订单明细事实关联商品维度。'),
('fact_order_item_to_dim_seller', 'dw.fact_order_item', 'seller_key', 'dw.dim_seller', 'seller_key', 'many_to_one', '订单明细事实关联卖家维度。'),
('fact_order_item_to_dim_status', 'dw.fact_order_item', 'order_status_key', 'dw.dim_order_status', 'order_status_key', 'many_to_one', '订单明细事实关联订单状态维度。'),
('fact_order_to_dim_customer', 'dw.fact_order', 'customer_key', 'dw.dim_customer', 'customer_key', 'many_to_one', '订单事实关联客户维度。'),
('fact_order_to_dim_date', 'dw.fact_order', 'purchase_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '订单事实按下单日期关联日期维度。'),
('fact_order_to_dim_status', 'dw.fact_order', 'order_status_key', 'dw.dim_order_status', 'order_status_key', 'many_to_one', '订单事实关联订单状态维度。'),
('fact_order_to_dim_approved_date', 'dw.fact_order', 'approved_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '订单事实按审批日期关联日期维度。'),
('fact_order_to_dim_delivered_carrier_date', 'dw.fact_order', 'delivered_carrier_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '订单事实按交付承运商日期关联日期维度。'),
('fact_order_to_dim_delivered_customer_date', 'dw.fact_order', 'delivered_customer_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '订单事实按客户实际收货日期关联日期维度。'),
('fact_order_to_dim_estimated_delivery_date', 'dw.fact_order', 'estimated_delivery_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '订单事实按预计送达日期关联日期维度。'),
('fact_order_to_fact_order_item_by_order', 'dw.fact_order', 'order_id', 'dw.fact_order_item', 'order_id', 'filter_exists', '按商品、品类或卖家条件筛选订单时使用 EXISTS，避免订单与多条明细直接 JOIN 后重复计数。'),
('fact_order_to_fact_payment_by_order', 'dw.fact_order', 'order_id', 'dw.fact_payment', 'order_id', 'filter_exists', '按支付条件筛选订单时使用 EXISTS，避免一个订单多条支付记录造成重复计数。'),
('fact_order_to_fact_review_by_order', 'dw.fact_order', 'order_id', 'dw.fact_review', 'order_id', 'filter_exists', '按评价条件筛选订单时使用 EXISTS，避免一个订单多条评价记录造成重复计数。'),
('fact_payment_to_dim_customer', 'dw.fact_payment', 'customer_key', 'dw.dim_customer', 'customer_key', 'many_to_one', '支付事实关联客户维度。'),
('fact_payment_to_dim_date', 'dw.fact_payment', 'purchase_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '支付事实按订单下单日期关联日期维度。'),
('fact_payment_to_dim_payment_type', 'dw.fact_payment', 'payment_type_key', 'dw.dim_payment_type', 'payment_type_key', 'many_to_one', '支付事实关联支付方式维度。'),
('fact_review_to_dim_customer', 'dw.fact_review', 'customer_key', 'dw.dim_customer', 'customer_key', 'many_to_one', '评价事实关联客户维度。'),
('fact_review_to_dim_creation_date', 'dw.fact_review', 'review_creation_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '评价事实按评价创建日期关联日期维度。'),
('fact_review_to_dim_answer_date', 'dw.fact_review', 'review_answer_date_key', 'dw.dim_date', 'date_key', 'many_to_one', '评价事实按评价回复日期关联日期维度。');

INSERT INTO metrics (
  metric_id, metric_name, business_name, base_table_id, expression_sql,
  aggregation_type, calculation_grain, aggregation_rule, unit, description
) VALUES
('gmv', 'gmv', '销售额', 'dw.fact_order_item', 'SUM(price)', 'sum', '一个 order_id + order_item_id 一行', '直接汇总订单明细金额；可以按订单明细可安全关联的商品、品类、卖家和日期维度分析；不要为了订单属性直接 JOIN 一对多事实表。', 'currency', '商品成交金额之和，第一版口径不含运费。'),
('freight_amount', 'freight_amount', '运费金额', 'dw.fact_order_item', 'SUM(freight_value)', 'sum', '一个 order_id + order_item_id 一行', '直接汇总订单明细运费；按商品、品类、卖家和日期维度分析时保留订单明细粒度。', 'currency', '订单明细运费金额之和。'),
('order_count', 'order_count', '订单量', 'dw.fact_order', 'SUM(order_count)', 'sum', '一个 order_id 一行', '只能在订单事实粒度上汇总；不要直接 JOIN fact_order_item、fact_payment 或 fact_review 后再 SUM(order_count)，否则一条订单会被一对多明细展开而重复计数。', 'order', '订单事实表每行一个订单，按 order_count 求和。'),
('item_count', 'item_count', '商品明细数', 'dw.fact_order_item', 'SUM(item_count)', 'sum', '一个 order_id + order_item_id 一行', '直接汇总订单明细行数；可以按订单明细侧可安全关联的商品、品类和卖家维度分析。', 'item', '订单明细事实表每行一个商品明细，按 item_count 求和。'),
('avg_order_value', 'avg_order_value', '平均客单价', 'dw.fact_order_item', 'SUM(price) / COUNT(DISTINCT order_id)', 'ratio', '按当前分析维度聚合后的订单明细集合', '分子是订单明细销售额，分母是当前分组内去重订单数；不要与 fact_order 的订单量直接 JOIN 后聚合。', 'currency/order', '商品成交金额除以当前分组内的去重订单数。'),
('payment_amount', 'payment_amount', '支付金额', 'dw.fact_payment', 'SUM(payment_value)', 'sum', '一条订单支付记录一行', '直接汇总支付记录；不要与商品明细或评价事实直接 JOIN 后聚合，跨事实表筛选使用 EXISTS。', 'currency', '支付记录金额之和。'),
('avg_review_score', 'avg_review_score', '平均评分', 'dw.fact_review', 'AVG(review_score)', 'avg', '一条评价记录一行', '按评价记录计算平均评分；不要与商品明细、支付记录直接 JOIN 后聚合。', 'score', '评价分数平均值。'),
('good_review_rate', 'good_review_rate', '好评率', 'dw.fact_review', 'SUM(CASE WHEN review_score >= 4 THEN 1 ELSE 0 END) / COUNT(*)', 'ratio', '一条评价记录一行', '按评价记录计算评分大于等于 4 的占比；跨订单条件筛选使用 EXISTS。', 'percent', '评分大于等于 4 的评价占比。'),
('delay_rate', 'delay_rate', '延迟率', 'dw.fact_order', 'SUM(CASE WHEN is_delayed = 1 THEN 1 ELSE 0 END) / COUNT(*)', 'ratio', '一个 order_id 一行', '只能在订单事实粒度计算；不要直接 JOIN 一对多订单明细、支付或评价后聚合。', 'percent', '实际送达晚于预计送达的订单占比。'),
('avg_delivery_days', 'avg_delivery_days', '平均配送天数', 'dw.fact_order', 'AVG(customer_delivery_days)', 'avg', '一个 order_id 一行', '只能在订单事实粒度计算；不要直接 JOIN 一对多事实表后聚合。', 'day', '下单到客户收货的平均天数。');

UPDATE tables SET aliases = JSON_ARRAY(), status = 'active';
UPDATE columns SET aliases = JSON_ARRAY(), status = 'active';
UPDATE metrics SET aliases = JSON_ARRAY(), status = 'active';

INSERT INTO dimensions VALUES
('purchase_month', 'purchase_month', '下单月份', 'dw.dim_date', 'year_month_value', '按下单日期所属月份分析。'),
('product_category', 'product_category', '商品品类', 'dw.dim_category', 'category_display_name', '按商品品类分析。'),
('customer_state', 'customer_state', '客户州', 'dw.dim_customer', 'customer_state', '按客户所在州分析。'),
('seller_state', 'seller_state', '卖家州', 'dw.dim_seller', 'seller_state', '按卖家所在州分析。'),
('order_status', 'order_status', '订单状态', 'dw.dim_order_status', 'order_status', '按订单状态分析。'),
('payment_type', 'payment_type', '支付方式', 'dw.dim_payment_type', 'payment_type', '按支付方式分析。'),
('review_score', 'review_score', '评分', 'dw.fact_review', 'review_score', '按评价分数分析。'),
('is_delayed', 'is_delayed', '是否延迟', 'dw.fact_order', 'is_delayed', '按是否延迟送达分析。');

INSERT INTO metric_dimensions
SELECT
  metric_id,
  dimension_id,
  '已登记为支持的指标与维度组合。',
  'supported',
  '按照指标基础表粒度和已登记关系建立查询，避免跨事实表直接 JOIN 后聚合。'
FROM metrics
JOIN dimensions
WHERE
  (metric_id IN ('gmv', 'freight_amount', 'item_count', 'avg_order_value') AND dimension_id IN ('purchase_month', 'product_category', 'customer_state', 'seller_state', 'order_status'))
  OR (metric_id IN ('order_count', 'delay_rate', 'avg_delivery_days') AND dimension_id IN ('purchase_month', 'customer_state', 'order_status', 'is_delayed'))
  OR (metric_id IN ('payment_amount') AND dimension_id IN ('purchase_month', 'payment_type', 'customer_state'))
  OR (metric_id IN ('avg_review_score', 'good_review_rate') AND dimension_id IN ('purchase_month', 'review_score', 'customer_state', 'product_category'));

-- 明确登记两个高风险组合，避免模型把“没有兼容记录”误解为“没有额外说明”。
-- 订单量的基础粒度是 fact_order 的一个订单一行；卖家州和商品品类来自订单明细侧，
-- 不能通过 fact_order -> fact_order_item 的 filter_exists 关系直接成为订单量的分组维度。
INSERT INTO metric_dimensions (
  metric_id, dimension_id, compatibility_note, support_level, usage_note
) VALUES
(
  'order_count',
  'seller_state',
  '订单量不支持直接按卖家州拆分。',
  'unsupported',
  '卖家州来自 fact_order_item 侧；一个订单可能包含多个卖家州，直接 JOIN 会展开订单并改变订单量口径。若业务必须分析，应先明确“包含该卖家州商品的去重订单数”这一新口径。'
),
(
  'order_count',
  'product_category',
  '订单量不支持直接按商品品类拆分。',
  'unsupported',
  '商品品类来自 fact_order_item 侧；一个订单可能包含多个商品品类，直接 JOIN 会展开订单并改变订单量口径。若业务必须分析，应先明确按品类归属的去重订单数口径。'
);

INSERT INTO subject_areas VALUES
('sales', '销售分析', '围绕销售额、订单量、商品销量、客单价进行分析。'),
('orders', '订单分析', '围绕订单量、订单状态和履约过程进行分析。'),
('payments', '支付分析', '围绕支付金额、支付方式和分期进行分析。'),
('reviews', '评价分析', '围绕评分、好评率和差评进行分析。'),
('logistics', '物流分析', '围绕配送时长、延迟率和履约表现进行分析。');

INSERT INTO subject_area_metrics VALUES
('sales', 'gmv'), ('sales', 'freight_amount'), ('sales', 'item_count'), ('sales', 'avg_order_value'),
('orders', 'order_count'), ('orders', 'delay_rate'),
('payments', 'payment_amount'),
('reviews', 'avg_review_score'), ('reviews', 'good_review_rate'),
('logistics', 'delay_rate'), ('logistics', 'avg_delivery_days');

INSERT INTO subject_area_dimensions VALUES
('sales', 'purchase_month'), ('sales', 'product_category'), ('sales', 'customer_state'), ('sales', 'seller_state'), ('sales', 'order_status'),
('orders', 'purchase_month'), ('orders', 'customer_state'), ('orders', 'order_status'),
('payments', 'purchase_month'), ('payments', 'payment_type'), ('payments', 'customer_state'),
('reviews', 'purchase_month'), ('reviews', 'review_score'), ('reviews', 'customer_state'), ('reviews', 'product_category'),
('logistics', 'purchase_month'), ('logistics', 'customer_state'), ('logistics', 'seller_state'), ('logistics', 'is_delayed');

INSERT INTO business_terms VALUES
('gmv', '销售额', '第一版销售额采用商品成交金额 SUM(price)，不含运费。', 'gmv'),
('aov', '平均客单价', '商品成交金额除以去重订单数。', 'avg_order_value'),
('delay_order', '延迟订单', '实际送达时间晚于预计送达时间的订单。', 'delay_rate'),
('good_review', '好评', '评分大于等于 4 分的评价。', 'good_review_rate');

INSERT INTO query_examples VALUES
('sales_by_month_category', '按月份和品类看销售额', 'SELECT d.year_month_value, c.category_display_name, SUM(f.price) AS gmv FROM dw.fact_order_item f JOIN dw.dim_date d ON f.purchase_date_key = d.date_key JOIN dw.dim_product p ON f.product_key = p.product_key LEFT JOIN dw.dim_category c ON p.category_key = c.category_key GROUP BY d.year_month_value, c.category_display_name ORDER BY d.year_month_value, gmv DESC;', '销售额来自 fact_order_item.price，月份来自 dim_date，品类来自 dim_category。'),
('delay_rate_by_month', '按月份看延迟率', 'SELECT d.year_month_value, SUM(CASE WHEN f.is_delayed = 1 THEN 1 ELSE 0 END) / COUNT(*) AS delay_rate FROM dw.fact_order f JOIN dw.dim_date d ON f.purchase_date_key = d.date_key GROUP BY d.year_month_value ORDER BY d.year_month_value;', '延迟率来自 fact_order.is_delayed，月份按下单日期统计。'),
('orders_by_month', '按月份看订单量', 'SELECT d.year_month_value, COUNT(*) AS order_count FROM dw.fact_order f JOIN dw.dim_date d ON f.purchase_date_key = d.date_key GROUP BY d.year_month_value ORDER BY d.year_month_value;', '订单量来自 fact_order，每行代表一个订单。'),
('orders_by_status', '按订单状态看订单量', 'SELECT os.order_status, COUNT(*) AS order_count FROM dw.fact_order f JOIN dw.dim_order_status os ON f.order_status_key = os.order_status_key GROUP BY os.order_status ORDER BY order_count DESC;', '订单状态来自 dim_order_status，用于查看不同状态的订单分布。'),
('payment_by_type', '按支付方式看支付金额', 'SELECT pt.payment_type, SUM(fp.payment_value) AS payment_amount FROM dw.fact_payment fp JOIN dw.dim_payment_type pt ON fp.payment_type_key = pt.payment_type_key GROUP BY pt.payment_type ORDER BY payment_amount DESC;', '支付金额来自 fact_payment，支付方式来自 dim_payment_type。'),
('delivery_by_month', '按月份看平均配送天数', 'SELECT d.year_month_value, AVG(f.customer_delivery_days) AS avg_delivery_days FROM dw.fact_order f JOIN dw.dim_date d ON f.purchase_date_key = d.date_key GROUP BY d.year_month_value ORDER BY d.year_month_value;', '平均配送天数来自 fact_order.customer_delivery_days。'),
('top_categories_by_gmv', '找出销售额最高的前10个品类', 'SELECT c.category_display_name, SUM(oi.price) AS gmv FROM dw.fact_order_item oi JOIN dw.dim_product p ON oi.product_key = p.product_key LEFT JOIN dw.dim_category c ON p.category_key = c.category_key GROUP BY c.category_display_name ORDER BY gmv DESC LIMIT 10;', '品类来自 dim_category，销售额来自 fact_order_item.price。'),
('repeat_customer_rate', '计算复购客户占比', 'SELECT ROUND(SUM(CASE WHEN order_cnt > 1 THEN 1 ELSE 0 END) / COUNT(*), 4) AS repeat_customer_rate FROM (SELECT c.customer_unique_id, COUNT(*) AS order_cnt FROM dw.fact_order f JOIN dw.dim_customer c ON f.customer_key = c.customer_key GROUP BY c.customer_unique_id) x;', '按 dim_customer.customer_unique_id 统计每个唯一客户的下单次数。');
