-- 08_update_metric_aliases.sql
-- 作用：
--   为 meta.metrics 补充指标别名，用于后续 Qdrant 指标语义召回增强。
-- 输入：
--   05_build_meta_tables.sql 已经创建并初始化好的 meta.metrics。
-- 输出：
--   meta.metrics.aliases 补充指标常见叫法、英文缩写和同义表达。
-- 说明：
--   别名只增强检索，不改变 expression_sql 中定义的指标计算口径。
--   第一版只放有明确对应关系的叫法，避免含义过宽导致错误召回。

USE meta;

UPDATE metrics
SET aliases = JSON_ARRAY('平均配送时长', '平均送达天数', '平均物流时长', '平均收货时长'),
    status = 'active'
WHERE metric_id = 'avg_delivery_days';

UPDATE metrics
SET aliases = JSON_ARRAY('客单价', 'AOV', '平均订单金额', '每单平均金额'),
    status = 'active'
WHERE metric_id = 'avg_order_value';

UPDATE metrics
SET aliases = JSON_ARRAY('平均评价分数', '平均评价星级', '客户平均评分', '平均星级'),
    status = 'active'
WHERE metric_id = 'avg_review_score';

UPDATE metrics
SET aliases = JSON_ARRAY('配送延迟率', '订单延迟率', '超时率', '延迟订单占比'),
    status = 'active'
WHERE metric_id = 'delay_rate';

UPDATE metrics
SET aliases = JSON_ARRAY('运费总额', '物流费用', '配送费用', '邮费总额'),
    status = 'active'
WHERE metric_id = 'freight_amount';

UPDATE metrics
SET aliases = JSON_ARRAY('GMV', '成交金额', '销售金额', '商品成交额'),
    status = 'active'
WHERE metric_id = 'gmv';

UPDATE metrics
SET aliases = JSON_ARRAY('好评占比', '高评分占比', '四五星评价占比', '正面评价率'),
    status = 'active'
WHERE metric_id = 'good_review_rate';

UPDATE metrics
SET aliases = JSON_ARRAY('商品销量', '销售件数', '商品件数', '订单商品数量'),
    status = 'active'
WHERE metric_id = 'item_count';

UPDATE metrics
SET aliases = JSON_ARRAY('订单数', '订单数量', '下单量', '成交订单数'),
    status = 'active'
WHERE metric_id = 'order_count';

UPDATE metrics
SET aliases = JSON_ARRAY('支付总额', '付款金额', '实付金额', '付款总额'),
    status = 'active'
WHERE metric_id = 'payment_amount';

-- 防御性兜底：后续新增但尚未维护别名的指标保持空数组和 active。
UPDATE metrics
SET aliases = JSON_ARRAY()
WHERE aliases IS NULL;

UPDATE metrics
SET status = 'active'
WHERE status IS NULL OR status = '';
