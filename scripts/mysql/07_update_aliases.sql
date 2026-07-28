-- 07_update_aliases.sql
-- 作用：
--   为 meta.tables 和 meta.columns 补充 aliases，用于后续 Qdrant 向量检索召回增强。
-- 输入：
--   05_build_meta_tables.sql 已经创建并初始化好的 meta.tables、meta.columns。
-- 输出：
--   1. meta.tables.aliases 补充表级别名、同义词、业务叫法。
--   2. meta.columns.aliases 补充字段级别名、同义词、业务叫法。
-- 说明：
--   aliases 是语义检索增强数据，不改变 DW 表结构和指标口径。
--   第一版只放有把握的常见业务叫法，避免过宽别名导致错误召回。
--   status 目前统一保持 active。

USE meta;

-- 表级别名：帮助用户用“订单表、商品表、支付表”等自然说法召回对应 DW 表。
UPDATE tables
SET aliases = JSON_ARRAY('日期表', '时间维度', '时间表', '日历表'),
    status = 'active'
WHERE table_id = 'dw.dim_date';

UPDATE tables
SET aliases = JSON_ARRAY('客户表', '用户维度', '用户表', '买家维度'),
    status = 'active'
WHERE table_id = 'dw.dim_customer';

UPDATE tables
SET aliases = JSON_ARRAY('商品表', '产品维度', '产品表', '货品维度'),
    status = 'active'
WHERE table_id = 'dw.dim_product';

UPDATE tables
SET aliases = JSON_ARRAY('品类表', '类目维度', '分类维度', '商品分类'),
    status = 'active'
WHERE table_id = 'dw.dim_category';

UPDATE tables
SET aliases = JSON_ARRAY('卖家表', '商家维度', '商家表', '店铺维度'),
    status = 'active'
WHERE table_id = 'dw.dim_seller';

UPDATE tables
SET aliases = JSON_ARRAY('地区维度', '区域维度', '地理表', '城市州维度'),
    status = 'active'
WHERE table_id = 'dw.dim_location';

UPDATE tables
SET aliases = JSON_ARRAY('状态维度', '订单状态表', '订单状态字典'),
    status = 'active'
WHERE table_id = 'dw.dim_order_status';

UPDATE tables
SET aliases = JSON_ARRAY('支付方式表', '付款方式维度', '支付类型表'),
    status = 'active'
WHERE table_id = 'dw.dim_payment_type';

UPDATE tables
SET aliases = JSON_ARRAY('订单表', '订单主表', '订单事实表', '履约订单表'),
    status = 'active'
WHERE table_id = 'dw.fact_order';

UPDATE tables
SET aliases = JSON_ARRAY('订单明细表', '订单商品表', '商品销售明细', '销售明细表'),
    status = 'active'
WHERE table_id = 'dw.fact_order_item';

UPDATE tables
SET aliases = JSON_ARRAY('支付表', '付款表', '支付记录表', '订单支付表'),
    status = 'active'
WHERE table_id = 'dw.fact_payment';

UPDATE tables
SET aliases = JSON_ARRAY('评价表', '评论表', '用户评价表', '订单评价表'),
    status = 'active'
WHERE table_id = 'dw.fact_review';

-- 字段级别名：帮助用户用自然语言字段叫法召回真实 DW 字段。
UPDATE columns
SET aliases = JSON_ARRAY('成交金额', '商品金额', '销售金额', '商品价格'),
    status = 'active'
WHERE column_id = 'dw.fact_order_item.price';

UPDATE columns
SET aliases = JSON_ARRAY('运费', '物流费', '配送费', '邮费'),
    status = 'active'
WHERE column_id = 'dw.fact_order_item.freight_value';

UPDATE columns
SET aliases = JSON_ARRAY('商品数量', '明细数量', '销售件数', '商品件数'),
    status = 'active'
WHERE column_id = 'dw.fact_order_item.item_count';

UPDATE columns
SET aliases = JSON_ARRAY('订单量', '下单数', '订单数量', '成交订单数'),
    status = 'active'
WHERE column_id = 'dw.fact_order.order_count';

UPDATE columns
SET aliases = JSON_ARRAY('配送天数', '送达天数', '物流时长', '收货时长'),
    status = 'active'
WHERE column_id = 'dw.fact_order.customer_delivery_days';

UPDATE columns
SET aliases = JSON_ARRAY('延迟', '是否超时', '是否晚到', '延期送达'),
    status = 'active'
WHERE column_id = 'dw.fact_order.is_delayed';

UPDATE columns
SET aliases = JSON_ARRAY('付款金额', '实付金额', '支付总额', '付款总额'),
    status = 'active'
WHERE column_id = 'dw.fact_payment.payment_value';

UPDATE columns
SET aliases = JSON_ARRAY('评分', '评价星级', '客户评分', '满意度评分'),
    status = 'active'
WHERE column_id = 'dw.fact_review.review_score';

UPDATE columns
SET aliases = JSON_ARRAY('月份', '年月', '下单月份', '统计月份'),
    status = 'active'
WHERE column_id = 'dw.dim_date.year_month_value';

UPDATE columns
SET aliases = JSON_ARRAY('品类', '类目', '商品分类', '商品类别'),
    status = 'active'
WHERE column_id = 'dw.dim_category.category_display_name';

UPDATE columns
SET aliases = JSON_ARRAY('客户地区', '买家州', '买家地区', '用户地区'),
    status = 'active'
WHERE column_id = 'dw.dim_customer.customer_state';

UPDATE columns
SET aliases = JSON_ARRAY('卖家地区', '商家州', '商家地区', '店铺地区'),
    status = 'active'
WHERE column_id = 'dw.dim_seller.seller_state';

UPDATE columns
SET aliases = JSON_ARRAY('状态', '订单阶段', '履约状态', '发货状态'),
    status = 'active'
WHERE column_id = 'dw.dim_order_status.order_status';

UPDATE columns
SET aliases = JSON_ARRAY('付款方式', '支付类型', '付款类型', '支付渠道'),
    status = 'active'
WHERE column_id = 'dw.dim_payment_type.payment_type';

-- 防御性兜底：如果后面新增了表或字段但还没有维护 aliases，保持空数组和 active。
UPDATE tables
SET aliases = JSON_ARRAY()
WHERE aliases IS NULL;

UPDATE columns
SET aliases = JSON_ARRAY()
WHERE aliases IS NULL;

UPDATE tables SET status = 'active' WHERE status IS NULL OR status = '';
UPDATE columns SET status = 'active' WHERE status IS NULL OR status = '';
