"""记忆层的领域数据结构和端口协议。

领域层只依赖这些接口，不直接依赖 SQLAlchemy、Qdrant 或 Neo4j。这样未来
替换存储实现时，上下文工程不需要跟着重写。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.agent.memory.enums import (
    MemoryDecisionAction,
    MemoryFormationStatus,
    MemoryFormationTrigger,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)


@dataclass(frozen=True, slots=True)
class MemorySource:
    """一条记忆的业务来源。"""

    # 来源类型：message、turn、output 或 asset。
    source_type: str
    # 来源对象 ID。
    source_id: str
    # 来源对象内部的可选字段路径。
    source_path: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryCreate:
    """创建长期记忆所需的领域输入。"""

    # 记忆所属用户，用于所有查询隔离。
    user_id: str
    # 记忆类型：episodic、semantic 或 perceptual。
    memory_type: MemoryType
    # 可检索正文。
    content: str
    # 记忆可见范围。
    scope: MemoryScope = MemoryScope.USER
    # 产生记忆的会话 ID。
    conversation_id: str | None = None
    # 项目级记忆所属的项目 ID；user/conversation 范围时为空。
    project_id: str | None = None
    # 类型相关的结构化字段；Semantic 的图投影也从这里读取显式数据。
    structured_data: dict[str, Any] = field(default_factory=dict)
    # 记忆重要性，范围 0 到 1。
    importance: float = 0.5
    # 记忆可信度，范围 0 到 1。
    confidence: float = 0.5
    # 可选的自动过期时间。
    expires_at: datetime | None = None
    # 可选来源列表，用于审计和回溯。
    sources: tuple[MemorySource, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryAsset:
    """附件元数据记录。"""

    # 附件稳定 ID。
    asset_id: str
    # 上传附件的用户 ID。
    user_id: str
    # 附件所属会话 ID。
    conversation_id: str | None
    # 附件模态：当前启用 text，其他模态保留接口。
    modality: str
    # 原始文件名。
    file_name: str
    # 文件 MIME 类型。
    mime_type: str
    # 文件存储 URI。
    storage_uri: str
    # 提取后的文本内容。
    extracted_text: str | None
    # 内容提取状态。
    extraction_status: str
    # 向量索引状态。
    index_status: str
    # 附件大小、校验和等扩展元数据。
    metadata: dict[str, Any] = field(default_factory=dict)
    # 项目级附件所属项目 ID；项目外的附件不填写。
    project_id: str | None = None
    # 生成向量时使用的编码器名称；尚未索引时为空。
    encoder_name: str | None = None
    # 向量维度；尚未索引时为空。
    embedding_dimension: int | None = None
    # 附件记录创建时间；创建请求可以不提供。
    created_at: datetime | None = None
    # 附件记录最近更新时间；创建请求可以不提供。
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """已经持久化的长期记忆。"""

    # 记忆稳定 ID。
    memory_id: str
    # 记忆所属用户。
    user_id: str
    # 记忆类型。
    memory_type: MemoryType
    # 记忆范围。
    scope: MemoryScope
    # 可选会话 ID。
    conversation_id: str | None
    # 可选项目 ID。
    project_id: str | None
    # 可检索正文。
    content: str
    # 结构化扩展数据。
    structured_data: dict[str, Any]
    # 生命周期状态。
    status: MemoryStatus
    # 当前版本号。
    version: int
    # 记忆重要性。
    importance: float
    # 记忆可信度。
    confidence: float
    # 该版本替代的旧记忆 ID。
    supersedes_memory_id: str | None
    # 自动过期时间。
    expires_at: datetime | None
    # 创建时间。
    created_at: datetime
    # 最后更新时间。
    updated_at: datetime
    # 被检索命中的次数。
    access_count: int = 0
    # 最近一次被检索命中的时间。
    last_accessed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MemorySearchResult:
    """记忆检索结果及其来源分数。"""

    # 被召回的记忆。
    memory: MemoryRecord
    # 向量或关键词相似度。
    similarity: float
    # 最终排序分数。
    score: float
    # 召回来源；混合召回时使用逗号连接多个来源。
    source: str
    # 用于解释最终排序的分项信号，例如 vector、lexical、graph、reference。
    signals: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    """PostgreSQL 原子完成一次长期记忆写入后的结果。"""

    # 数据库在同一事务内决定的处理动作。
    action: MemoryDecisionAction
    # 当前 active 记录；duplicate 时是原记录，其他动作是新记录。
    record: MemoryRecord
    # replace 时被标记为 superseded 的旧记录。
    replaced_record: MemoryRecord | None = None


@dataclass(frozen=True, slots=True)
class MemoryFormationRecord:
    """PostgreSQL 中已存在的记忆形成审计快照。"""

    # 形成任务 ID；同一个 formation_key 始终复用这个 ID。
    formation_run_id: str
    # 由 run_id、turn_id、trigger 和 extractor_version 计算的幂等身份。
    formation_key: str
    # 形成任务当前状态。
    status: MemoryFormationStatus
    # 形成任务的触发方式。
    trigger: MemoryFormationTrigger
    # 已提取的候选数量。
    candidate_count: int = 0
    # 已接受并写入或替换的数量。
    accepted_count: int = 0
    # 被治理拒绝的数量。
    rejected_count: int = 0
    # 与现有记忆重复的数量。
    duplicate_count: int = 0
    # 替换旧版本的数量。
    replaced_count: int = 0
    # 处理失败的数量。
    failed_count: int = 0
    # 已保存的紧凑候选决定。
    decisions: list[dict[str, Any]] = field(default_factory=list)
    # 任务级错误信息。
    error_message: str = ""


class MemoryRepository(Protocol):
    """长期记忆事实仓储端口。"""

    async def write_managed(self, request: MemoryCreate) -> MemoryWriteResult: ...

    async def get(self, memory_id: str, user_id: str) -> MemoryRecord | None: ...

    async def touch_access(self, memory_ids: list[str], user_id: str) -> None: ...

    async def search(
        self,
        *,
        user_id: str,
        memory_type: MemoryType,
        query: str,
        limit: int,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[tuple[MemoryRecord, float]]: ...

    async def update(
        self, memory_id: str, user_id: str, **changes: Any
    ) -> MemoryRecord | None: ...

    async def mark_status(
        self, memory_id: str, user_id: str, status: MemoryStatus
    ) -> bool: ...

    async def list_expired(self, limit: int = 100) -> list[MemoryRecord]: ...

    async def save_asset(self, asset: MemoryAsset) -> MemoryAsset: ...

    async def get_asset(self, asset_id: str, user_id: str) -> MemoryAsset | None: ...

    async def list_sources(
        self, memory_id: str, user_id: str
    ) -> list[MemorySource]: ...

    async def update_asset(
        self, asset_id: str, user_id: str, **changes: Any
    ) -> MemoryAsset | None: ...

    async def save_graph_projection(
        self,
        memory: MemoryRecord,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        sync_status: str = "pending",
        last_error: str | None = None,
    ) -> None: ...

    async def record_index_failure(
        self, memory_id: str, target: str, error: str = ""
    ) -> None: ...

    async def get_formation_run(
        self, *, formation_key: str, user_id: str
    ) -> MemoryFormationRecord | None: ...

    async def create_formation_run(self, payload: dict[str, Any]) -> bool: ...

    async def update_formation_run(
        self, formation_run_id: str, **changes: Any
    ) -> None: ...

    async def source_exists(
        self,
        *,
        user_id: str,
        source_type: str,
        source_id: str,
        conversation_id: str | None = None,
    ) -> bool: ...

    async def update_graph_projection(
        self,
        memory_id: str,
        sync_status: str,
        error: str = "",
    ) -> None: ...


class VectorMemoryRepository(Protocol):
    """Qdrant 向量索引端口。"""

    async def upsert(self, memory: MemoryRecord, vector: list[float]) -> None: ...
    async def search(
        self,
        *,
        user_id: str,
        memory_type: MemoryType,
        vector: list[float],
        limit: int,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...
    async def delete(self, memory: MemoryRecord) -> None: ...


class GraphMemoryRepository(Protocol):
    """Neo4j Semantic Memory 图投影端口。"""

    async def upsert_projection(
        self,
        memory: MemoryRecord,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
    ) -> None: ...
    async def delete_projection(self, memory_id: str) -> None: ...

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]: ...


class MemoryContextReader(Protocol):
    """ContextEngine 唯一依赖的只读记忆端口。"""

    async def search(
        self,
        *,
        memory_type: MemoryType,
        user_id: str,
        query: str,
        limit: int = 5,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[MemorySearchResult]: ...

    async def search_many(
        self,
        *,
        memory_types: list[MemoryType],
        user_id: str,
        query: str,
        limit: int = 10,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[MemorySearchResult]: ...

    async def get_asset(self, asset_id: str, user_id: str) -> MemoryAsset | None: ...

    async def load_working(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[MemoryRecord]: ...

    async def get_sources(
        self, memory_id: str, user_id: str
    ) -> list[MemorySource]: ...
