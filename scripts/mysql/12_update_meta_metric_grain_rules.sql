-- 12_update_meta_metric_grain_rules.sql
-- 作用：
--   为已经创建的 meta 数据库补充指标计算粒度、聚合限制和指标维度支持级别。
-- 输入：
--   05_build_meta_tables.sql 已创建 metrics、dimensions 和 metric_dimensions。
-- 输出：
--   1. meta.metrics 记录指标的计算粒度与跨表聚合规则。
--   2. meta.metric_dimensions 记录 supported、conditional、unsupported 级别。
--   3. 明确登记订单量按卖家州、商品品类分析时的口径风险。
-- 说明：
--   本脚本兼容旧版 Meta。旧版缺少的字段会先补充，再更新已有数据。
--   support_level 只是提供给 SQL 生成 LLM 的语义依据，不在应用层直接硬拦截。

USE meta;

-- 旧版 metrics 没有指标级粒度说明时，先以可空字段完成平滑升级。
SET @metrics_calculation_grain_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'metrics'
    AND column_name = 'calculation_grain'
);
SET @add_metrics_calculation_grain_sql := IF(
  @metrics_calculation_grain_exists = 0,
  'ALTER TABLE metrics ADD COLUMN calculation_grain TEXT NULL COMMENT ''指标计算粒度'' AFTER aggregation_type',
  'SELECT 1'
);
PREPARE add_metrics_calculation_grain FROM @add_metrics_calculation_grain_sql;
EXECUTE add_metrics_calculation_grain;
DEALLOCATE PREPARE add_metrics_calculation_grain;

SET @metrics_aggregation_rule_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'metrics'
    AND column_name = 'aggregation_rule'
);
SET @add_metrics_aggregation_rule_sql := IF(
  @metrics_aggregation_rule_exists = 0,
  'ALTER TABLE metrics ADD COLUMN aggregation_rule TEXT NULL COMMENT ''指标聚合规则和粒度限制'' AFTER calculation_grain',
  'SELECT 1'
);
PREPARE add_metrics_aggregation_rule FROM @add_metrics_aggregation_rule_sql;
EXECUTE add_metrics_aggregation_rule;
DEALLOCATE PREPARE add_metrics_aggregation_rule;

-- 为每个已登记指标补充 expression_sql 之外的计算粒度和聚合限制。
UPDATE metrics
SET
  calculation_grain = CASE metric_id
    WHEN 'gmv' THEN '一个 order_id + order_item_id 一行'
    WHEN 'freight_amount' THEN '一个 order_id + order_item_id 一行'
    WHEN 'order_count' THEN '一个 order_id 一行'
    WHEN 'item_count' THEN '一个 order_id + order_item_id 一行'
    WHEN 'avg_order_value' THEN '按当前分析维度聚合后的订单明细集合'
    WHEN 'payment_amount' THEN '一条订单支付记录一行'
    WHEN 'avg_review_score' THEN '一条评价记录一行'
    WHEN 'good_review_rate' THEN '一条评价记录一行'
    WHEN 'delay_rate' THEN '一个 order_id 一行'
    WHEN 'avg_delivery_days' THEN '一个 order_id 一行'
    ELSE COALESCE(calculation_grain, '')
  END,
  aggregation_rule = CASE metric_id
    WHEN 'gmv' THEN '直接汇总订单明细金额；可以按订单明细可安全关联的商品、品类、卖家和日期维度分析；不要为了订单属性直接 JOIN 一对多事实表。'
    WHEN 'freight_amount' THEN '直接汇总订单明细运费；按商品、品类、卖家和日期维度分析时保留订单明细粒度。'
    WHEN 'order_count' THEN '只能在订单事实粒度上汇总；不要直接 JOIN fact_order_item、fact_payment 或 fact_review 后再 SUM(order_count)，否则一条订单会被一对多明细展开而重复计数。'
    WHEN 'item_count' THEN '直接汇总订单明细行数；可以按订单明细侧可安全关联的商品、品类和卖家维度分析。'
    WHEN 'avg_order_value' THEN '分子是订单明细销售额，分母是当前分组内去重订单数；不要与 fact_order 的订单量直接 JOIN 后聚合。'
    WHEN 'payment_amount' THEN '直接汇总支付记录；不要与商品明细或评价事实直接 JOIN 后聚合，跨事实表筛选使用 EXISTS。'
    WHEN 'avg_review_score' THEN '按评价记录计算平均评分；不要与商品明细、支付记录直接 JOIN 后聚合。'
    WHEN 'good_review_rate' THEN '按评价记录计算评分大于等于 4 的占比；跨订单条件筛选使用 EXISTS。'
    WHEN 'delay_rate' THEN '只能在订单事实粒度计算；不要直接 JOIN 一对多订单明细、支付或评价后聚合。'
    WHEN 'avg_delivery_days' THEN '只能在订单事实粒度计算；不要直接 JOIN 一对多事实表后聚合。'
    ELSE COALESCE(aggregation_rule, '')
  END
WHERE calculation_grain IS NULL OR calculation_grain = ''
   OR aggregation_rule IS NULL OR aggregation_rule = '';

ALTER TABLE metrics
  MODIFY COLUMN calculation_grain TEXT NOT NULL COMMENT '指标计算粒度',
  MODIFY COLUMN aggregation_rule TEXT NOT NULL COMMENT '指标聚合规则和粒度限制';

-- 旧版 metric_dimensions 没有支持级别和使用说明时，先补充字段。
SET @support_level_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'metric_dimensions'
    AND column_name = 'support_level'
);
SET @add_support_level_sql := IF(
  @support_level_exists = 0,
  'ALTER TABLE metric_dimensions ADD COLUMN support_level VARCHAR(32) NULL DEFAULT ''supported'' COMMENT ''支持级别'' AFTER compatibility_note',
  'SELECT 1'
);
PREPARE add_support_level FROM @add_support_level_sql;
EXECUTE add_support_level;
DEALLOCATE PREPARE add_support_level;

SET @usage_note_exists := (
  SELECT COUNT(*)
  FROM information_schema.columns
  WHERE table_schema = DATABASE()
    AND table_name = 'metric_dimensions'
    AND column_name = 'usage_note'
);
SET @add_usage_note_sql := IF(
  @usage_note_exists = 0,
  'ALTER TABLE metric_dimensions ADD COLUMN usage_note TEXT NULL COMMENT ''按该维度分析时的使用说明'' AFTER support_level',
  'SELECT 1'
);
PREPARE add_usage_note FROM @add_usage_note_sql;
EXECUTE add_usage_note;
DEALLOCATE PREPARE add_usage_note;

UPDATE metric_dimensions
SET
  support_level = COALESCE(NULLIF(support_level, ''), 'supported'),
  usage_note = COALESCE(NULLIF(usage_note, ''), '按照指标基础表粒度和已登记关系建立查询，避免跨事实表直接 JOIN 后聚合。');

-- 订单量来自一个订单一行；商品品类和卖家州来自订单明细侧。
-- 这里记录的是业务口径事实，不代表程序直接拒绝用户问题。
INSERT INTO metric_dimensions (
  metric_id, dimension_id, compatibility_note, support_level, usage_note
)
SELECT
  'order_count',
  d.dimension_id,
  CASE d.dimension_id
    WHEN 'seller_state' THEN '订单量不支持直接按卖家州拆分。'
    WHEN 'product_category' THEN '订单量不支持直接按商品品类拆分。'
  END,
  'unsupported',
  CASE d.dimension_id
    WHEN 'seller_state' THEN '卖家州来自 fact_order_item 侧；一个订单可能包含多个卖家州，直接 JOIN 会展开订单并改变订单量口径。若业务必须分析，应先明确“包含该卖家州商品的去重订单数”这一新口径。'
    WHEN 'product_category' THEN '商品品类来自 fact_order_item 侧；一个订单可能包含多个商品品类，直接 JOIN 会展开订单并改变订单量口径。若业务必须分析，应先明确按品类归属的去重订单数口径。'
  END
FROM dimensions AS d
WHERE d.dimension_id IN ('seller_state', 'product_category')
ON DUPLICATE KEY UPDATE
  compatibility_note = VALUES(compatibility_note),
  support_level = VALUES(support_level),
  usage_note = VALUES(usage_note);

ALTER TABLE metric_dimensions
  MODIFY COLUMN support_level ENUM('supported', 'conditional', 'unsupported') NOT NULL DEFAULT 'supported' COMMENT '支持级别：支持、有条件支持或不支持',
  MODIFY COLUMN usage_note TEXT NOT NULL COMMENT '按该维度分析时的使用说明';

-- 让 filter_exists 的事实关系本身也携带粒度风险说明，便于直接阅读 Meta。
UPDATE relationships
SET description = '按商品、品类或卖家条件筛选订单时使用 EXISTS；不能把明细侧字段直接作为订单事实的分组维度，避免订单与多条明细直接 JOIN 后重复计数。'
WHERE relationship_id = 'fact_order_to_fact_order_item_by_order';
