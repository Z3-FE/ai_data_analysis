"""当前会话和历史附件引用解析。"""

import re
from collections.abc import Sequence

from app.agent.context_engine.contracts import ContextRequest, ReferenceResolution
from app.agent.memory.interfaces import MemoryAsset, MemoryContextReader, MemoryRecord


class ContextReferenceResolver:
    """使用 Working 消息中的稳定对象 ID 解析自然语言指代。"""

    _history_cues = (
        "刚才",
        "上面",
        "继续",
        "接着",
        "这个",
        "那个",
        "它",
        "上述",
        "前面",
        "上一个",
        "上一轮",
        "上次",
        "之前",
        "以前",
        "previous",
        "continue",
    )
    _asset_cues = (
        "附件",
        "文件",
        "文档",
        "图片",
        "图",
        "截图",
        "音频",
        "视频",
        "表格",
        "attachment",
        "file",
        "image",
    )
    _ordinal_values = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }

    def __init__(self, memory_reader: MemoryContextReader) -> None:
        self.memory_reader = memory_reader

    async def resolve(
        self, request: ContextRequest, working: Sequence[MemoryRecord]
    ) -> ReferenceResolution:
        """解析并校验本轮直接附件以及可唯一确定的历史附件。"""
        query = request.query.strip().lower()
        needs_working = any(cue in query for cue in self._history_cues)
        mentions_asset = any(cue in query for cue in self._asset_cues)
        current_ids = tuple(dict.fromkeys(request.asset_ids))
        valid_current, current_errors = await self._validate_assets(
            current_ids, request.user_id
        )

        historical_groups = self._historical_asset_groups(working)
        historical_ids: tuple[str, ...] = ()
        unresolved = list(current_errors)
        if not current_ids and mentions_asset:
            historical_ids, reference_error = await self._resolve_historical_assets(
                query=query,
                groups=historical_groups,
                user_id=request.user_id,
                prefers_latest=needs_working,
            )
            if reference_error:
                unresolved.append(reference_error)

        valid_historical, historical_errors = await self._validate_assets(
            historical_ids, request.user_id
        )
        unresolved.extend(historical_errors)
        resolved_ids = tuple(dict.fromkeys((*valid_current, *valid_historical)))
        return ReferenceResolution(
            asset_ids=resolved_ids,
            historical_asset_ids=valid_historical,
            needs_working_context=needs_working,
            unresolved_references=tuple(dict.fromkeys(unresolved)),
        )

    async def _resolve_historical_assets(
        self,
        *,
        query: str,
        groups: list[tuple[str, ...]],
        user_id: str,
        prefers_latest: bool,
    ) -> tuple[tuple[str, ...], str]:
        """按文件名、明确序号或最近一组的顺序解析历史附件。"""
        if not groups:
            return (), "问题引用了历史附件，但当前会话没有可用附件 ID"
        # groups 是倒序消息组；扁平列表恢复成用户上传的时间顺序。
        all_ids = tuple(asset_id for group in reversed(groups) for asset_id in group)
        named_matches: list[str] = []
        for asset_id in all_ids:
            asset = await self.memory_reader.get_asset(asset_id, user_id)
            if asset is not None and asset.file_name.casefold() in query.casefold():
                named_matches.append(asset.asset_id)
        if len(named_matches) == 1:
            return (named_matches[0],), ""
        if len(named_matches) > 1:
            return (), "问题中的文件名匹配到多个历史附件"

        ordinal = self._ordinal(query)
        if ordinal is not None:
            # 同一轮上传多个附件时，“第二张”优先指向最近一组的第二项。
            candidates = groups[0] if len(groups[0]) >= ordinal else all_ids
            if ordinal <= len(candidates):
                return (candidates[ordinal - 1],), ""
            return (), f"问题引用了第 {ordinal} 个附件，但历史附件数量不足"

        if len(groups) == 1:
            return groups[0], ""
        if prefers_latest and len(groups[0]) == 1:
            return groups[0], ""
        return (), "问题引用了附件，但无法唯一确定历史中的哪一个附件"

    @classmethod
    def _ordinal(cls, query: str) -> int | None:
        match = re.search(
            r"第\s*(\d+|[一二两三四五六七八九十])\s*(?:个|份|张|段)?",
            query,
        )
        if match is None:
            return None
        raw = match.group(1)
        return int(raw) if raw.isdigit() else cls._ordinal_values.get(raw)

    @staticmethod
    def _historical_asset_groups(
        working: Sequence[MemoryRecord],
    ) -> list[tuple[str, ...]]:
        """按消息倒序返回历史附件组，同一消息中的附件保持关联。"""
        groups: list[tuple[str, ...]] = []
        seen: set[tuple[str, ...]] = set()
        for record in reversed(working):
            raw_ids = record.structured_data.get("asset_ids", [])
            if not isinstance(raw_ids, (list, tuple)):
                continue
            group = tuple(
                dict.fromkeys(
                    str(asset_id).strip()
                    for asset_id in raw_ids
                    if str(asset_id).strip()
                )
            )
            if group and group not in seen:
                seen.add(group)
                groups.append(group)
        return groups

    async def _validate_assets(
        self, asset_ids: Sequence[str], user_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        valid: list[str] = []
        errors: list[str] = []
        for asset_id in asset_ids:
            asset: MemoryAsset | None = await self.memory_reader.get_asset(
                asset_id, user_id
            )
            if asset is None:
                errors.append(f"附件 {asset_id} 不存在或当前用户无权访问")
            else:
                valid.append(asset.asset_id)
        return tuple(valid), tuple(errors)


__all__ = ["ContextReferenceResolver"]
