"""Semantic Memory 的 Neo4j 约束和索引。"""

from typing import Any


async def ensure_graph_schema(driver: Any, database: str) -> None:
    """创建图投影需要的唯一约束和名称索引。"""
    async with driver.session(database=database) as session:
        result = await session.run(
            "CREATE CONSTRAINT memory_entity_key IF NOT EXISTS "
            "FOR (entity:MemoryEntity) REQUIRE entity.entity_id IS UNIQUE"
        )
        await result.consume()
        result = await session.run(
            "CREATE CONSTRAINT memory_node_key IF NOT EXISTS "
            "FOR (memory:Memory) REQUIRE memory.memory_id IS UNIQUE"
        )
        await result.consume()
        result = await session.run(
            "CREATE INDEX memory_entity_name IF NOT EXISTS "
            "FOR (entity:MemoryEntity) ON (entity.name)"
        )
        await result.consume()
        result = await session.run(
            "CREATE INDEX memory_entity_user_source IF NOT EXISTS "
            "FOR (entity:MemoryEntity) ON (entity.user_id, entity.source_entity_id)"
        )
        await result.consume()
        result = await session.run(
            "CREATE INDEX memory_user_scope IF NOT EXISTS "
            "FOR (memory:Memory) ON (memory.user_id, memory.scope)"
        )
        await result.consume()
