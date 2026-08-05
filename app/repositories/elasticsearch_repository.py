"""Elasticsearch 仓库。

这一层负责维度值的物理索引创建、批量写入、alias 切换和全文检索。
客户端本身只保留连接能力，真正的业务动作都收口到这里。
"""

from collections.abc import Iterable
from typing import Any

from app.entities.es.es_dimension_value import (
    EsDimensionValueDocument,
    EsDimensionValueHit,
)

from elasticsearch import AsyncElasticsearch


class ElasticsearchRepository:
    """封装 Elasticsearch 索引与查询操作。"""

    def __init__(self, client: AsyncElasticsearch) -> None:
        self.client = client

    @property
    def sdk(self):
        """暴露底层 SDK，便于仓库内部统一调用。"""
        return self.client

    def recreate_dimension_values_index(self, index_name: str) -> None:
        """重建维度值物理索引，并显式定义字段类型和检索方式。"""
        # 先删掉旧索引，避免 mapping 改了以后和旧结构冲突。
        if self.sdk.indices.exists(index=index_name):
            self.sdk.indices.delete(index=index_name)

        # 这里直接创建新的物理索引，后面再通过 alias 指向它。
        self.sdk.indices.create(
            index=index_name,
            settings={"number_of_shards": 1, "number_of_replicas": 0},
            mappings={
                "dynamic": "strict",
                "properties": {
                    # 维度值唯一标识，用来作为文档主键和融合去重 key。
                    "value_id": {"type": "keyword"},
                    # 维度英文标识，比如 payment_type、order_status。
                    "dimension_id": {"type": "keyword"},
                    # 维度内部名称，通常保持系统定义。
                    "dimension_name": {"type": "keyword"},
                    # 维度中文业务名，用于检索和给 LLM 提示更自然的表达。
                    "dimension_business_name": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    # 所属字段 ID，方便回溯维度值来自哪一列。
                    "column_id": {"type": "keyword"},
                    # 字段英文名。
                    "column_name": {"type": "keyword"},
                    # 所属表 ID。
                    "table_id": {"type": "keyword"},
                    # 所属表名。
                    "table_name": {"type": "keyword"},
                    # 数据库名，比如 dw 或 meta。
                    "database_name": {"type": "keyword"},
                    # 字段数据类型。
                    "data_type": {"type": "keyword"},
                    # 数据库中的原始值，优先作为精确匹配目标。
                    "raw_value": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    # 规范化后的值，用于去除格式差异后的检索。
                    "normalized_value": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    # 标准中文展示名，比如“信用卡支付”。
                    "display_name": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    # 别名、同义词、口语表达，用于扩展召回。
                    "aliases": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    # 说明文本，用来补足语义信息。
                    "description": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                    },
                    # 这个值出现的次数，便于后续统计或排序。
                    "value_count": {"type": "long"},
                    # 是否允许参与语义检索。
                    "semantic_enabled": {"type": "boolean"},
                    # 当前值是否启用。
                    "status": {"type": "keyword"},
                },
            },
        )

    def bulk_index(self, index_name: str, documents: Iterable[dict[str, Any]]) -> int:
        """分批写入 ES，文档 `_id` 使用稳定的 value_id。"""
        # 先把可迭代对象转成列表，方便按批次切片。
        document_list = list(documents)
        from app.core.config import settings

        batch_size = settings.elasticsearch.bulk_batch_size
        indexed_count = 0
        for start in range(0, len(document_list), batch_size):
            # bulk 的 operations 需要“操作指令 + 文档本体”交替出现。
            operations: list[dict[str, Any]] = []
            for document in document_list[start : start + batch_size]:
                # 用 value_id 做文档主键，重复导入时会覆盖同一条值记录。
                operations.append(
                    {"index": {"_index": index_name, "_id": document["value_id"]}}
                )
                operations.append(document)
            # refresh="wait_for" 让写入后立即可查，适合本地构建阶段。
            response = self.sdk.bulk(operations=operations, refresh="wait_for")
            if response.get("errors"):
                # 只截取前几条失败项，避免错误信息过长。
                failures = [
                    item for item in response["items"] if item["index"].get("error")
                ]
                raise RuntimeError(f"Elasticsearch 批量写入失败：{failures[:3]}")
            indexed_count += len(operations) // 2
        return indexed_count

    def switch_alias(self, alias_name: str, index_name: str) -> None:
        """把稳定 alias 原子切换到新物理索引。"""
        # 先收集需要移除和新增的 alias 操作，再一次性原子提交。
        actions: list[dict[str, Any]] = []
        if self.sdk.indices.exists_alias(name=alias_name):
            # 如果 alias 已经指向旧索引，先把旧索引解绑。
            current = self.sdk.indices.get_alias(name=alias_name)
            actions.extend(
                {"remove": {"index": old_index, "alias": alias_name}}
                for old_index in current.keys()
            )
        actions.append({"add": {"index": index_name, "alias": alias_name}})
        self.sdk.indices.update_aliases(actions=actions)

    def count(self, index_name: str) -> int:
        """返回索引或 alias 下的文档数量。"""
        # 这里直接返回 count 结果，方便构建脚本和校验脚本使用。
        return int(self.sdk.count(index=index_name)["count"])

    def search_dimension_values(
        self,
        query_text: str,
        index_name: str,
        limit: int,
    ) -> list[EsDimensionValueHit]:
        """同时执行真实值、中文名称、别名精确匹配和全文匹配。"""
        # 先做精确匹配，再用全文检索补召回，保证“准”和“广”都兼顾。
        response = self.sdk.search(
            index=index_name,
            size=limit,
            query={
                "bool": {
                    "filter": [{"term": {"status": "active"}}],
                    "should": [
                        # 原始值精确命中，最优先。
                        {
                            "term": {
                                "raw_value.keyword": {
                                    "value": query_text,
                                    "boost": 20,
                                    "_name": "raw_value_exact",
                                }
                            }
                        },
                        # 中文标准名精确命中。
                        {
                            "term": {
                                "display_name.keyword": {
                                    "value": query_text,
                                    "boost": 16,
                                    "_name": "display_name_exact",
                                }
                            }
                        },
                        # 别名精确命中。
                        {
                            "term": {
                                "aliases.keyword": {
                                    "value": query_text,
                                    "boost": 14,
                                    "_name": "alias_exact",
                                }
                            }
                        },
                        # 全文匹配作为补充召回通道。
                        {
                            "multi_match": {
                                "query": query_text,
                                "fields": [
                                    "display_name^4",
                                    "aliases^3",
                                    "description^2",
                                    "normalized_value^2",
                                    "raw_value",
                                ],
                                "type": "best_fields",
                                "_name": "full_text",
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
        )
        return [
            EsDimensionValueHit(
                document=EsDimensionValueDocument(**dict(hit["_source"])),
                score=float(hit["_score"]),
                matched_queries=list(hit.get("matched_queries", [])),
            )
            for hit in response["hits"]["hits"]
        ]

    async def async_search_dimension_values(
        self,
        query_text: str,
        index_name: str,
        limit: int,
    ) -> list[EsDimensionValueHit]:
        """异步执行真实值、中文名称、别名精确匹配和全文匹配。"""
        # 异步版本给 LangGraph 节点和 API 层使用，避免阻塞事件循环。
        response = await self.sdk.search(
            index=index_name,
            size=limit,
            query={
                "bool": {
                    # status 是 keyword 类型，term 不分词，只检索当前启用的维度值。
                    "filter": [{"term": {"status": "active"}}],
                    "should": [
                        # 1. 数据库真实值精确匹配，例如查询 credit_card。
                        # .keyword 是 raw_value 的精确匹配子字段，不会对查询词分词。
                        {
                            "term": {
                                "raw_value.keyword": {
                                    "value": query_text,
                                    "boost": 20,
                                    "_name": "raw_value_exact",
                                }
                            }
                        },
                        # 2. 中文展示名精确匹配，例如查询“信用卡支付”。
                        # 必须与 display_name.keyword 保存的完整值一致。
                        {
                            "term": {
                                "display_name.keyword": {
                                    "value": query_text,
                                    "boost": 16,
                                    "_name": "display_name_exact",
                                }
                            }
                        },
                        # 3. 别名精确匹配，例如 aliases 中存在“刷卡支付”。
                        # aliases 是数组，但 term 会逐个比较数组中的完整 keyword 值。
                        {
                            "term": {
                                "aliases.keyword": {
                                    "value": query_text,
                                    "boost": 14,
                                    "_name": "alias_exact",
                                }
                            }
                        },
                        # 4. 多字段全文匹配：对 text 字段分词后检索，用于补充召回。
                        # ^4、^3、^2 是字段权重；best_fields 使用命中效果最好的字段计分。
                        {
                            "multi_match": {
                                "query": query_text,
                                "fields": [
                                    "display_name^4",
                                    "aliases^3",
                                    "description^2",
                                    "normalized_value^2",
                                    "raw_value",
                                ],
                                "type": "best_fields",
                                "_name": "full_text",
                            }
                        },
                    ],
                    # 四种 should 查询至少命中一种，否则该文档不会进入结果。
                    "minimum_should_match": 1,
                }
            },
        )
        return [
            EsDimensionValueHit(
                document=EsDimensionValueDocument(**dict(hit["_source"])),
                score=float(hit["_score"]),
                matched_queries=list(hit.get("matched_queries", [])),
            )
            for hit in response["hits"]["hits"]
        ]
