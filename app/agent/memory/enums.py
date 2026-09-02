"""记忆层共享枚举。"""

from enum import StrEnum


class MemoryType(StrEnum):
    """四类记忆的业务标识。"""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PERCEPTUAL = "perceptual"


class MemoryScope(StrEnum):
    """记忆可见范围。"""

    USER = "user"
    CONVERSATION = "conversation"
    PROJECT = "project"


class MemoryStatus(StrEnum):
    """长期记忆生命周期状态。"""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"
    EXPIRED = "expired"
