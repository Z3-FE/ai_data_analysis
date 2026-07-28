"""Agent 相关接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_agent_service
from app.services.agent_service import AgentService

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    """最小 Agent 请求体。"""

    input_text: str = Field(..., description="输入给 Agent 的文本")


class AgentRunResponse(BaseModel):
    """最小 Agent 响应体。"""

    input_text: str
    original_question: str
    llm_keywords: list[str]
    jieba_keywords: list[str]
    keywords: list[str]
    column_recall_terms: list[str]
    column_candidates: list[dict]
    output_text: str
    llm_output: str


@router.post("/run", response_model=AgentRunResponse)
def run_agent(
    payload: AgentRunRequest,
    agent_service: Annotated[AgentService, Depends(get_agent_service)],
) -> AgentRunResponse:
    """执行当前 LangGraph 并返回结果。"""
    result = agent_service.run(payload.input_text)
    return AgentRunResponse.model_validate(result)


@router.post("/run/stream")
async def run_agent_stream(
    payload: AgentRunRequest,
    agent_service: Annotated[AgentService, Depends(get_agent_service)],
) -> StreamingResponse:
    """以 SSE 形式返回 Agent 结果。"""
    return StreamingResponse(
        agent_service.qyStream(payload.input_text),
        media_type="text/event-stream",
    )
