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


class MemoryFormationTrigger(StrEnum):
    """一次长期记忆形成任务的触发方式。"""

    EXPLICIT = "explicit_request"
    AUTOMATIC = "automatic"
    SKIPPED = "skipped"


class MemoryFormationStatus(StrEnum):
    """记忆形成审计任务的生命周期状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED = "failed"


class MemoryDecisionAction(StrEnum):
    """后端对单条记忆候选做出的最终决定。"""

    CREATED = "created"
    REPLACED = "replaced"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"
