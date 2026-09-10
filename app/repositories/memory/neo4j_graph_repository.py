"""Neo4j Semantic Memory 图投影仓储。"""

from typing import Any

from neo4j import AsyncDriver

from app.agent.memory.interfaces import MemoryRecord
from app.core.config import Neo4jConfig


def _safe_relation_type(value: str) -> str:
    """把外部关系类型转换为合法的 Cypher 标识符。"""
    cleaned = "".join(
        char if char.isascii() and (char.isalnum() or char == "_") else "_"
        for char in value.upper()
    )
    if not cleaned:
        return "RELATED_TO"
    return f"REL_{cleaned}" if cleaned[0].isdigit() else cleaned


def _entity_key(user_id: str, entity_id: str) -> str:
    """为实体增加用户命名空间，避免不同用户的同名实体互相合并。"""
    return f"{user_id}::{entity_id}"


class Neo4jGraphRepository:
    """写入 Semantic Memory 的图投影，并按用户检索实体命中的记忆。"""

    def __init__(self, driver: AsyncDriver, config: Neo4jConfig) -> None:
        # 复用应用生命周期中的 Neo4j driver。
        self.driver = driver
        # 图数据库名称。
        self.database = config.database

    async def upsert_projection(
        self,
        memory: MemoryRecord,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> None:
        """幂等写入记忆节点、实体节点和记忆专属关系。"""
        async with self.driver.session(database=self.database) as session:

            async def write_projection(tx: Any) -> None:
                result = await tx.run(
                    "MERGE (memory:Memory {memory_id: $memory_id}) "
                    "SET memory.user_id = $user_id, "
                    "memory.scope = $scope, "
                    "memory.conversation_id = $conversation_id, "
                    "memory.project_id = $project_id, "
                    "memory.status = $status",
                    memory_id=memory.memory_id,
                    user_id=memory.user_id,
                    scope=memory.scope.value,
                    # Neo4j 的 SET null 会删除属性；空串可以保持统一属性 Schema。
                    conversation_id=memory.conversation_id or "",
                    project_id=memory.project_id or "",
                    status=memory.status.value,
                )
                await result.consume()
                # MENTIONS 和实体关系都带 memory_id；更新时先清掉旧投影。
                result = await tx.run(
                    "MATCH ()-[relation {memory_id: $memory_id}]->() DELETE relation",
                    memory_id=memory.memory_id,
                )
                await result.consume()
                for entity in entities:
                    raw_entity_id = str(entity.get("entity_id") or "").strip()
                    if not raw_entity_id:
                        continue
                    entity_id = _entity_key(memory.user_id, raw_entity_id)
                    result = await tx.run(
                        "MERGE (entity:MemoryEntity {entity_id: $entity_id}) "
                        "SET entity.name = $name, "
                        "entity.entity_type = $entity_type, "
                        "entity.source_entity_id = $source_entity_id, "
                        "entity.user_id = $user_id",
                        entity_id=entity_id,
                        name=str(entity.get("name") or raw_entity_id),
                        entity_type=str(entity.get("entity_type") or "unknown"),
                        source_entity_id=raw_entity_id,
                        user_id=memory.user_id,
                    )
                    await result.consume()
                    result = await tx.run(
                        "MATCH (memory:Memory {memory_id: $memory_id}) "
                        "MATCH (entity:MemoryEntity {entity_id: $entity_id}) "
                        "MERGE (memory)-[mention:MENTIONS]->(entity) "
                        "SET mention.memory_id = $memory_id",
                        memory_id=memory.memory_id,
                        entity_id=entity_id,
                    )
                    await result.consume()
                for relation in relations:
                    raw_source_id = str(relation.get("source_id") or "").strip()
                    raw_target_id = str(relation.get("target_id") or "").strip()
                    if not raw_source_id or not raw_target_id:
                        continue
                    source_id = _entity_key(memory.user_id, raw_source_id)
                    target_id = _entity_key(memory.user_id, raw_target_id)
                    relation_type = _safe_relation_type(
                        str(relation.get("relation_type") or "RELATED_TO")
                    )
                    result = await tx.run(
                        f"MATCH (source:MemoryEntity {{entity_id: $source_id}}), "
                        f"(target:MemoryEntity {{entity_id: $target_id}}) "
                        f"MERGE (source)-[relation:{relation_type} {{memory_id: $memory_id}}]->(target)",
                        source_id=source_id,
                        target_id=target_id,
                        memory_id=memory.memory_id,
                    )
                    await result.consume()
                # 更新后清理不再被任何记忆引用的旧实体。
                result = await tx.run(
                    "MATCH (entity:MemoryEntity {user_id: $user_id}) "
                    "WHERE NOT (entity)<-[:MENTIONS]-(:Memory) "
                    "DETACH DELETE entity",
                    user_id=memory.user_id,
                )
                await result.consume()

            await session.execute_write(write_projection)

    async def delete_projection(self, memory_id: str) -> None:
        """删除记忆投影，并清理不再被任何记忆提及的实体。"""
        async with self.driver.session(database=self.database) as session:

            async def delete_projection(tx: Any) -> None:
                result = await tx.run(
                    "MATCH (memory:Memory {memory_id: $memory_id}) "
                    "RETURN memory.user_id AS user_id",
                    memory_id=memory_id,
                )
                owner = await result.single()
                result = await tx.run(
                    "MATCH ()-[relation {memory_id: $memory_id}]->() DELETE relation",
                    memory_id=memory_id,
                )
                await result.consume()
                result = await tx.run(
                    "MATCH (memory:Memory {memory_id: $memory_id}) "
                    "DETACH DELETE memory",
                    memory_id=memory_id,
                )
                await result.consume()
                if owner is not None and owner["user_id"]:
                    result = await tx.run(
                        "MATCH (entity:MemoryEntity {user_id: $user_id}) "
                        "WHERE NOT (entity)<-[:MENTIONS]-(:Memory) "
                        "DETACH DELETE entity",
                        user_id=owner["user_id"],
                    )
                    await result.consume()

            await session.execute_write(delete_projection)

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按实体名称或实体键命中当前用户的 Semantic Memory。"""
        if not query.strip():
            return []
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                "MATCH (seed:MemoryEntity {user_id: $user_id}) "
                "WHERE toLower(seed.name) CONTAINS toLower($search_text) "
                "OR toLower($search_text) CONTAINS toLower(seed.name) "
                "OR toLower(seed.source_entity_id) CONTAINS toLower($search_text) "
                "OR toLower($search_text) CONTAINS toLower(seed.source_entity_id) "
                "CALL (seed) { "
                "  RETURN seed AS entity, 1.0 AS relation_score "
                "  UNION "
                "  MATCH (seed)-[semantic_relation]-(related:MemoryEntity {user_id: $user_id}) "
                "  WHERE semantic_relation.memory_id IS NOT NULL "
                "  MATCH (relation_memory:Memory {memory_id: semantic_relation.memory_id, user_id: $user_id}) "
                "  WHERE relation_memory.status = 'active' "
                "  AND (relation_memory.scope = 'user' "
                "  OR (relation_memory.scope = 'conversation' "
                "  AND $conversation_id IS NOT NULL "
                "  AND relation_memory.conversation_id = $conversation_id) "
                "  OR (relation_memory.scope = 'project' "
                "  AND $project_id IS NOT NULL "
                "  AND relation_memory.project_id = $project_id)) "
                "  RETURN related AS entity, 0.6 AS relation_score "
                "} "
                "MATCH (memory:Memory {user_id: $user_id})-[:MENTIONS]->(entity) "
                "WHERE memory.status = 'active' "
                "AND (memory.scope = 'user' "
                "OR (memory.scope = 'conversation' "
                "AND $conversation_id IS NOT NULL "
                "AND memory.conversation_id = $conversation_id) "
                "OR (memory.scope = 'project' "
                "AND $project_id IS NOT NULL "
                "AND memory.project_id = $project_id)) "
                "RETURN memory.memory_id AS memory_id, "
                "max(relation_score) AS relation_score, "
                "count(DISTINCT entity) AS matches "
                "ORDER BY relation_score DESC, matches DESC LIMIT $limit",
                user_id=user_id,
                search_text=query.strip(),
                conversation_id=conversation_id,
                project_id=project_id,
                limit=max(0, limit),
            )
            records = [record async for record in result]
        return [
            {
                "memory_id": record["memory_id"],
                "score": min(
                    1.0,
                    float(record["relation_score"])
                    * (0.8 + 0.05 * int(record["matches"])),
                ),
            }
            for record in records
        ]
