"""会话创建和历史读取接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.dependencies import get_conversation_repository
from app.core.config import settings
from app.repositories.conversation_repository import ConversationRepository

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationCreateRequest(BaseModel):
    """新建会话时保存的应用级信息。"""

    title: str = Field(default="新建会话", max_length=255)
    data_source_id: str = Field(default="olist", max_length=128)
    adopted_semantic_draft_ids: list[str] = Field(default_factory=list)
    adopted_semantic_draft_titles: list[str] = Field(default_factory=list)


@router.post("")
async def create_conversation(
    payload: ConversationCreateRequest,
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
) -> dict:
    """创建一个新的聊天会话。"""
    conversation = await repository.create_conversation(
        user_id=settings.app.default_user_id,
        data_source_id=payload.data_source_id,
        title=payload.title,
        metadata={
            "adopted_semantic_draft_ids": payload.adopted_semantic_draft_ids,
            "adopted_semantic_draft_titles": payload.adopted_semantic_draft_titles,
        },
    )
    return conversation


@router.get("")
async def get_conversations(
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
    conversation_id: str | None = Query(default=None),
    include_messages: bool = Query(default=True),
) -> dict:
    """无 ID 返回列表，有 ID 返回可恢复的会话详情。"""
    if conversation_id:
        detail = await repository.get_conversation(
            user_id=settings.app.default_user_id,
            conversation_id=conversation_id,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if not include_messages:
            detail.pop("messages", None)
            detail.pop("outputs", None)
        return detail

    return {
        "conversations": await repository.list_conversations(
            user_id=settings.app.default_user_id
        )
    }


@router.get("/execution-trace")
async def get_execution_trace(
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
    conversation_id: str = Query(...),
    turn_id: str = Query(...),
) -> dict:
    """读取指定轮次的执行过程，供历史消息点击后恢复执行面板。"""
    output = await repository.get_execution_trace(
        user_id=settings.app.default_user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    return {
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "available": output is not None,
        "payload": output["payload"] if output else {"events": []},
    }


@router.delete("")
async def delete_conversation(
    repository: Annotated[ConversationRepository, Depends(get_conversation_repository)],
    conversation_id: str = Query(...),
) -> dict:
    """删除当前用户的会话及其历史内容。"""
    deleted = await repository.delete_conversation(
        user_id=settings.app.default_user_id,
        conversation_id=conversation_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"conversation_id": conversation_id, "deleted": True}
