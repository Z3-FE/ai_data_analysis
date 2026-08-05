"""Agent 在线维度值全文检索。"""

from elasticsearch import AsyncElasticsearch

from app.entities.es.es_dimension_value import (
    EsDimensionValueDocument,
    EsDimensionValueHit,
)


class DimensionValueSearch:
    """只负责 Agent 查询阶段的 ES 维度值召回。"""

    def __init__(self, client: AsyncElasticsearch) -> None:
        self.client = client

    async def search(
        self,
        query_text: str,
        index_name: str,
        limit: int,
    ) -> list[EsDimensionValueHit]:
        """执行精确匹配和全文匹配，并返回 ES 命中实体。"""
        response = await self.client.search(
            index=index_name,
            size=limit,
            query={
                "bool": {
                    # 只召回当前启用的维度值。
                    "filter": [{"term": {"status": "active"}}],
                    "should": [
                        # 数据库真实值精确匹配，优先级最高。
                        {"term": {"raw_value.keyword": {"value": query_text, "boost": 20, "_name": "raw_value_exact"}}},
                        # 中文展示名称精确匹配。
                        {"term": {"display_name.keyword": {"value": query_text, "boost": 16, "_name": "display_name_exact"}}},
                        # 别名精确匹配。aliases 是数组时，会逐个比较完整值。
                        {"term": {"aliases.keyword": {"value": query_text, "boost": 14, "_name": "alias_exact"}}},
                        # text 字段分词后的全文匹配，用于补充召回。
                        {
                            "multi_match": {
                                "query": query_text,
                                "fields": ["display_name^4", "aliases^3", "description^2", "normalized_value^2", "raw_value"],
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
