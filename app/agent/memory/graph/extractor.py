"""Semantic Memory 图投影输入解析。"""

from app.agent.memory.graph.models import GraphEntity, GraphRelation
from app.agent.memory.interfaces import MemoryRecord


class GraphExtractor:
    """只接受结构化数据中明确声明的实体和关系，不从自然语言猜测。"""

    def extract(
        self, memory: MemoryRecord
    ) -> tuple[list[GraphEntity], list[GraphRelation]]:
        """清洗 entities/relations，避免任意对象直接进入 Cypher。"""
        data = memory.structured_data or {}
        raw_entities = data.get("entities", [])
        raw_relations = data.get("relations", [])
        entities: list[GraphEntity] = []
        entity_ids: set[str] = set()
        for item in raw_entities if isinstance(raw_entities, list) else []:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or item.get("name") or "").strip()
            if not entity_id or entity_id in entity_ids:
                continue
            entity_ids.add(entity_id)
            entities.append(
                {
                    "entity_id": entity_id,
                    "name": str(item.get("name") or entity_id).strip(),
                    "entity_type": str(item.get("entity_type") or "unknown").strip(),
                }
            )

        relations: list[GraphRelation] = []
        for item in raw_relations if isinstance(raw_relations, list) else []:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("source_id") or "").strip()
            target_id = str(item.get("target_id") or "").strip()
            relation_type = str(item.get("relation_type") or "RELATED_TO").strip()
            if (
                not source_id
                or not target_id
                or source_id not in entity_ids
                or target_id not in entity_ids
            ):
                continue
            relations.append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "relation_type": relation_type or "RELATED_TO",
                }
            )
        return entities, relations
