# 第一阶段数据准备

## 数据来源

- 数据集：Brazilian E-Commerce Public Dataset by Olist
- Kaggle 地址：https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
- 本地原始数据目录：`data/raw/olist/`
- 本地下载缓存：`/private/tmp/olist-brazilian-ecommerce.zip`
- PostgreSQL 数据库：`enterprise_data_ai`
- PostgreSQL schema：`olist`

这份数据是第一阶段的主数据源，用来建设“企业级数据智能分析与决策平台”的交易分析底座。它不是文档 RAG 数据，而是结构化业务数据，后续更适合导入 PostgreSQL，再围绕数据库做 Text-to-SQL、指标解释、归因分析和多智能体分析。

## 文件清单

下面的行数来自 `wc -l`，包含表头。

| 文件 | 行数 | 主要含义 |
| --- | ---: | --- |
| `olist_orders_dataset.csv` | 99,442 | 订单主表，包含订单状态、购买时间、审批时间、发货时间、送达时间等 |
| `olist_order_items_dataset.csv` | 112,651 | 订单明细表，包含订单商品、卖家、价格、运费等 |
| `olist_order_payments_dataset.csv` | 103,887 | 支付表，包含支付方式、分期数、支付金额等 |
| `olist_order_reviews_dataset.csv` | 104,720 | 评价表，包含评分、评价创建时间、回复时间等 |
| `olist_customers_dataset.csv` | 99,442 | 客户表，包含客户匿名 ID、邮编、城市、州等 |
| `olist_products_dataset.csv` | 32,952 | 商品表，包含品类、尺寸、重量等 |
| `olist_sellers_dataset.csv` | 3,096 | 卖家表，包含卖家邮编、城市、州等 |
| `olist_geolocation_dataset.csv` | 1,000,164 | 地理位置表，包含邮编、经纬度、城市、州等 |
| `product_category_name_translation.csv` | 71 | 商品品类葡萄牙语到英语的翻译表 |

## 导入 PostgreSQL

在项目目录执行：

```bash
cd 00_LangGraph_Study/09_enterprise_data_intelligence_platform
bash scripts/import_olist_to_postgres.sh
```

脚本会自动做这些事：

- 如果本地没有 `enterprise_data_ai` 数据库，则自动创建。
- 删除并重建 `olist` schema，保证每次导入都是干净状态。
- 创建 typed tables，例如 `olist.orders`、`olist.customers`、`olist.order_items`。
- 使用 `psql \copy` 把 CSV 导入 PostgreSQL。
- 执行 `ANALYZE`，让 PostgreSQL 更新统计信息，方便后续查询优化。

如果你想换数据库名，可以这样执行：

```bash
POSTGRES_DB=your_database_name bash scripts/import_olist_to_postgres.sh
```

导入后可以执行验证 SQL：

```bash
psql -d enterprise_data_ai -f scripts/02_validate_olist_import.sql
```

当前导入结果：

| 表 | 行数 |
| --- | ---: |
| `olist.customers` | 99,441 |
| `olist.geolocation` | 1,000,163 |
| `olist.order_items` | 112,650 |
| `olist.order_payments` | 103,886 |
| `olist.order_reviews` | 99,224 |
| `olist.orders` | 99,441 |
| `olist.products` | 32,951 |
| `olist.sellers` | 3,095 |
| `olist.product_category_translation` | 71 |

## 第一阶段可以做什么

第一阶段重点不是“导入一个 PDF 让 AI 读”，而是先把真实业务数据变成可分析资产。

- 订单分析：订单量、成交金额、订单状态分布、订单趋势。
- 商品分析：品类销售排行、客单价、商品维度与销售表现。
- 客户分析：城市、州维度的客户分布和购买表现。
- 卖家分析：卖家地区分布、卖家销售贡献、订单履约表现。
- 支付分析：支付方式占比、分期付款行为、支付金额结构。
- 物流分析：发货耗时、配送耗时、预计送达与实际送达差异。
- 评价分析：评分分布、低评分订单定位、物流与评分之间的关系。

## 后续工程方向

建议后续按这个顺序推进：

1. 建立 PostgreSQL 业务库，把 CSV 导入正式表。
2. 使用 SQLAlchemy 定义业务模型，使用 Alembic 管理表结构迁移。
3. 建立语义层，定义指标、维度、表关系、常用分析口径。
4. 建立 Text-to-SQL Agent，根据用户自然语言生成 SQL。
5. 建立多智能体分析流程，例如意图识别、SQL 生成、数据校验、图表建议、业务解读、结论审查。
6. 后续再接入文档资料，把 PDF/报告中的指标定义、业务规则、分析模板沉淀到语义层，而不是把 PDF 当成项目主线。

## 类比前端理解

这份 Olist 数据就像前端项目里的后端接口数据源。

- CSV 文件：类似后端返回的原始接口数据。
- PostgreSQL：类似正式 API 服务背后的数据库。
- 语义层：类似前端的 service/model 层，把后端字段转换成业务可理解的概念。
- Text-to-SQL Agent：类似一个能根据用户操作自动拼请求参数的智能查询层。
- 多智能体分析流程：类似复杂页面里的多个模块协作，筛选条件、表格、图表、详情面板各自负责一部分，但最终服务同一个用户问题。
