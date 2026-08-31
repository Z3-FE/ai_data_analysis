"""独立日常聊天接口。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import get_daily_chat_service
from app.core.config import settings
from app.services.daily_chat_service import DailyChatService

router = APIRouter(prefix="/daily-chat", tags=["daily-chat"])


class DailyChatRequest(BaseModel):
    """独立日常聊天入口的请求体。"""

    input_text: str = Field(..., min_length=1, description="日常聊天问题")
    conversation_id: str = Field(..., min_length=1, description="当前会话 ID")


class DailyChatResponse(BaseModel):
    """日常聊天回答和上下文调试信息。"""

    conversation_id: str
    thread_id: str
    turn_id: str
    run_id: str
    execution_mode: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


@router.post("", response_model=DailyChatResponse)
async def run_daily_chat(
    payload: DailyChatRequest,
    service: Annotated[DailyChatService, Depends(get_daily_chat_service)],
) -> DailyChatResponse:
    """执行独立日常聊天，不进入数据分析 Agent 图。"""
    try:
        result = await service.chat(
            input_text=payload.input_text,
            conversation_id=payload.conversation_id,
            user_id=settings.app.default_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DailyChatResponse.model_validate(result)
