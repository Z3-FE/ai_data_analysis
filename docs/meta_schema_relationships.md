# Meta 元数据关系图

图例：实线表示 MySQL 真实外键；虚线表示程序约定的逻辑引用，不是数据库外键。

## 1. MySQL 真实外键

~~~mermaid
erDiagram
    DATA_SOURCES ||--o{ TABLES : data_source_id
    TABLES ||--o{ COLUMNS : table_id
    TABLES ||--o{ RELATIONSHIPS : from_table_id_to_table_id
    TABLES ||--o{ METRICS : base_table_id
    TABLES ||--o{ DIMENSIONS : table_id
    METRICS ||--o{ METRIC_DIMENSIONS : metric_id
    DIMENSIONS ||--o{ METRIC_DIMENSIONS : dimension_id
    SUBJECT_AREAS ||--o{ SUBJECT_AREA_METRICS : subject_area_id
    METRICS ||--o{ SUBJECT_AREA_METRICS : metric_id
    SUBJECT_AREAS ||--o{ SUBJECT_AREA_DIMENSIONS : subject_area_id
    DIMENSIONS ||--o{ SUBJECT_AREA_DIMENSIONS : dimension_id
    METRICS ||--o{ BUSINESS_TERMS : related_metric_id
~~~

真实外键来自 scripts/mysql/05_build_meta_tables.sql：

| 子表字段 | 父表字段 |
| --- | --- |
| tables.data_source_id | data_sources.data_source_id |
| columns.table_id | tables.table_id |
| relationships.from_table_id | tables.table_id |
| relationships.to_table_id | tables.table_id |
| metrics.base_table_id | tables.table_id |
| dimensions.table_id | tables.table_id |
| metric_dimensions.metric_id | metrics.metric_id |
| metric_dimensions.dimension_id | dimensions.dimension_id |
| subject_area_metrics.subject_area_id | subject_areas.subject_area_id |
| subject_area_metrics.metric_id | metrics.metric_id |
| subject_area_dimensions.subject_area_id | subject_areas.subject_area_id |
| subject_area_dimensions.dimension_id | dimensions.dimension_id |
| business_terms.related_metric_id | metrics.metric_id |

query_examples 当前没有外键。

## 2. relationships 的字段级逻辑引用

relationships 的表字段组合不是数据库 FK，而是程序用来定位 columns 的约定：

~~~mermaid
flowchart LR
    R[meta.relationships]
    RF[from_table_id + from_column_name]
    RT[to_table_id + to_column_name]
    CF[meta.columns: table_id + column_name]
    CT[meta.columns: table_id + column_name]
    SQL[SQL 连接条件]
    R --> RF
    R --> RT
    RF -. 程序校验/逻辑定位 .-> CF
    RT -. 程序校验/逻辑定位 .-> CT
    CF --> SQL
    CT --> SQL
~~~

例如：

~~~text
relationships.from_table_id = dw.fact_payment
relationships.from_column_name = payment_type_key
relationships.to_table_id = dw.dim_payment_type
relationships.to_column_name = payment_type_key
~~~

程序还需要定位两条 columns 记录：

~~~text
meta.columns.table_id = dw.fact_payment
meta.columns.column_name = payment_type_key

meta.columns.table_id = dw.dim_payment_type
meta.columns.column_name = payment_type_key
~~~

最后才能形成 SQL 条件：

~~~sql
dw.fact_payment.payment_type_key = dw.dim_payment_type.payment_type_key
~~~

因此，旧图只画 relationships -> tables 是不完整的：它表达了“关联哪两张表”，但没有表达“具体关联哪两个字段”。

## 3. dimensions 的字段级逻辑引用

dimensions 也保存 table_id 和 column_name，用来定位对应的 meta.columns：

~~~text
dimensions.table_id + dimensions.column_name
    -> meta.columns.table_id + meta.columns.column_name
~~~

这同样不是数据库 FK。meta.columns 上的 UNIQUE(table_id, column_name) 使这组条件理论上只能定位一条字段记录。

## 4. 对 Agent 合并和 SQL 生成的影响

~~~text
表/字段/指标/维度值召回
        ↓
得到候选 table_id
        ↓
按 table_id 加载 relationships
        ↓
按关系两端的 table_id + column_name 定位 meta.columns
        ↓
把关系字段补入对应 table_info.columns
        ↓
生成 SQL 上下文
~~~

不能只把关系两端的表传给模型，还要确保关系两端字段出现在对应的 table_info.columns 中，否则模型可能看到关系里有 payment_type_key，但表字段列表里没有它。

