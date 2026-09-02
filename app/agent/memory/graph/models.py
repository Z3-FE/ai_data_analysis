"""Neo4j Semantic Memory 图投影使用的轻量数据结构。"""

from typing import TypedDict


class GraphEntity(TypedDict, total=False):
    """一个需要投影到 Neo4j 的实体。"""

    # 用于跨记忆合并实体的稳定键。
    entity_id: str
    # 面向用户展示的实体名称。
    name: str
    # 业务实体类型，例如 product、person 或 concept。
    entity_type: str


class GraphRelation(TypedDict, total=False):
    """两个实体之间的有向关系。"""

    # 关系起点实体键。
    source_id: str
    # 关系终点实体键。
    target_id: str
    # Cypher 关系类型，会在仓储层经过标识符清洗。
    relation_type: str
