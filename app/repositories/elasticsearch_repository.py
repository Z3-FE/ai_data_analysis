"""Elasticsearch 仓库。

这一层负责维度值的物理索引创建、批量写入、alias 切换和全文检索。
客户端本身只保留连接能力，真正的业务动作都收口到这里。
"""

from collections.abc import Iterable
from typing import Any

from app.clients.elasticsearch_client import FullTextSearchClient


class ElasticsearchRepository:
    """封装 Elasticsearch 索引与查询操作。"""

    def __init__(self, client: FullTextSearchClient) -> None:
        self.client = client

    @property
    def sdk(self):
        """暴露底层 SDK，便于仓库内部统一调用。"""
        return self.client.client

    def recreate_dimension_values_index(self, index_name: str) -> None:
        """重建维度值物理索引，并配置精确匹配与中外文全文匹配字段。"""
        if self.sdk.indices.exists(index=index_name):
            self.sdk.indices.delete(index=index_name)

        self.sdk.indices.create(
            index=index_name,
            settings={"number_of_shards": 1, "number_of_replicas": 0},
            mappings={
                "dynamic": "strict",
                "properties": {
                    "value_id": {"type": "keyword"},
                    "dimension_id": {"type": "keyword"},
                    "dimension_name": {"type": "keyword"},
                    "dimension_business_name": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "column_id": {"type": "keyword"},
                    "column_name": {"type": "keyword"},
                    "table_id": {"type": "keyword"},
                    "table_name": {"type": "keyword"},
                    "database_name": {"type": "keyword"},
                    "data_type": {"type": "keyword"},
                    "raw_value": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "normalized_value": {
                        "type": "text",
                        "analyzer": "standard",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "display_name": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "aliases": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                        "fields": {"keyword": {"type": "keyword"}},
                    },
                    "description": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                    },
                    "value_count": {"type": "long"},
                    "semantic_enabled": {"type": "boolean"},
                    "status": {"type": "keyword"},
                },
            },
        )

    def bulk_index(self, index_name: str, documents: Iterable[dict[str, Any]]) -> int:
        """分批写入 ES，文档 `_id` 使用稳定的 value_id。"""
        document_list = list(documents)
        from app.core.config import settings

        batch_size = settings.elasticsearch.bulk_batch_size
        indexed_count = 0
        for start in range(0, len(document_list), batch_size):
            operations: list[dict[str, Any]] = []
            for document in document_list[start : start + batch_size]:
                operations.append(
                    {"index": {"_index": index_name, "_id": document["value_id"]}}
                )
                operations.append(document)
            response = self.sdk.bulk(operations=operations, refresh="wait_for")
            if response.get("errors"):
                failures = [
                    item for item in response["items"] if item["index"].get("error")
                ]
                raise RuntimeError(f"Elasticsearch 批量写入失败：{failures[:3]}")
            indexed_count += len(operations) // 2
        return indexed_count

    def switch_alias(self, alias_name: str, index_name: str) -> None:
        """把稳定 alias 原子切换到新物理索引。"""
        actions: list[dict[str, Any]] = []
        if self.sdk.indices.exists_alias(name=alias_name):
            current = self.sdk.indices.get_alias(name=alias_name)
            actions.extend(
                {"remove": {"index": old_index, "alias": alias_name}}
                for old_index in current.keys()
            )
        actions.append({"add": {"index": index_name, "alias": alias_name}})
        self.sdk.indices.update_aliases(actions=actions)

    def count(self, index_name: str) -> int:
        """返回索引或 alias 下的文档数量。"""
        return int(self.sdk.count(index=index_name)["count"])

    def search_dimension_values(
        self,
        query_text: str,
        index_name: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """同时执行真实值、中文名称、别名精确匹配和全文匹配。"""
        response = self.sdk.search(
            index=index_name,
            size=limit,
            query={
                "bool": {
                    "filter": [{"term": {"status": "active"}}],
                    "should": [
                        {
                            "term": {
                                "raw_value.keyword": {
                                    "value": query_text,
                                    "boost": 20,
                                    "_name": "raw_value_exact",
                                }
                            }
                        },
                        {
                            "term": {
                                "display_name.keyword": {
                                    "value": query_text,
                                    "boost": 16,
                                    "_name": "display_name_exact",
                                }
                            }
                        },
                        {
                            "term": {
                                "aliases.keyword": {
                                    "value": query_text,
                                    "boost": 14,
                                    "_name": "alias_exact",
                                }
                            }
                        },
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
            {
                "id": hit["_id"],
                "score": hit["_score"],
                "matched_queries": hit.get("matched_queries", []),
                "source": hit["_source"],
            }
            for hit in response["hits"]["hits"]
        ]
