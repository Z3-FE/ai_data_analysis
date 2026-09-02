"""Episodic Memory：保存已经发生过的具体任务、过程和结果。"""

from app.agent.memory.enums import MemoryType
from app.agent.memory.interfaces import MemoryCreate
from app.agent.memory.types.base import PersistentMemory


class EpisodicMemory(PersistentMemory):
    """面向历史任务经验的长期记忆。"""

    def __init__(self, **kwargs) -> None:
        super().__init__(memory_type=MemoryType.EPISODIC, **kwargs)

    async def add_episode(self, request: MemoryCreate):
        """语义上更明确的情景记忆写入别名。"""
        return await self.add(request)
