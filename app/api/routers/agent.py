"""Agent 相关接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_agent_service
from app.services.agent_service import AgentService

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    """当前聊天工作台提交给 Agent 的请求体。"""

    input_text: str = Field(..., description="输入给 Agent 的文本")
    session_id: str = Field(default="", description="当前聊天会话 ID")


class AgentRunResponse(BaseModel):
    """最小 Agent 响应体。"""

    input_text: str
    session_id: str = ""
    original_question: str
    execution_mode: str = "single_query"
    route_reason: str = ""
    analysis_goals: list[str] = Field(default_factory=list)
    route_confidence: float = 0.0
    clarification_question: str = ""
    analysis_plan: dict = Field(default_factory=dict)
    analysis_task_results: list[dict] = Field(default_factory=list)
    analysis_evidence: dict = Field(default_factory=dict)
    report_plan: dict = Field(default_factory=dict)
    report_plan_status: str = ""
    report_plan_error: str = ""
    rendered_report: dict = Field(default_factory=dict)
    llm_keywords: list[str]
    jieba_keywords: list[str]
    keywords: list[str]
    column_recall_terms: list[str]
    column_candidates: list[dict]
    dimension_infos: list[dict]
    output_text: str
    llm_output: str
    sql: str = ""
    sql_reasoning: str = ""
    sql_result: list[dict] = Field(default_factory=list)
    result_columns: list[dict] = Field(default_factory=list)
    dimension_value_mappings: list[dict] = Field(default_factory=list)
    display_sql_result: list[dict] = Field(default_factory=list)
    mapping_limitations: list[str] = Field(default_factory=list)


@router.post("/run", response_model=AgentRunResponse)
def run_agent(
    payload: AgentRunRequest,
    agent_service: Annotated[AgentService, Depends(get_agent_service)],
) -> AgentRunResponse:
    """执行当前 LangGraph 并返回结果。"""
    result = agent_service.run(payload.input_text, payload.session_id)
    return AgentRunResponse.model_validate(result)


@router.post("/run/stream")
async def run_agent_stream(
    payload: AgentRunRequest,
    agent_service: Annotated[AgentService, Depends(get_agent_service)],
) -> StreamingResponse:
    """以 SSE 形式返回 Agent 结果。"""
    return StreamingResponse(
        agent_service.qyStream(payload.input_text, payload.session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
