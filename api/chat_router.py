"""客服对话 API。"""

import openai
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.logger import logger
from agent.main_graph import process_query
from agent.state import AgentResponse

router = APIRouter()


class ChatRequest(BaseModel):
    """对话请求。"""

    query: str = Field(min_length=1, description="用户问题")
    session_id: str = Field(min_length=1, description="会话ID，多轮对话必须复用")
    user_id: str | None = Field(default=None, description="用户ID")


@router.post("/chat", response_model=AgentResponse)
async def chat(request: ChatRequest) -> AgentResponse:
    """处理一轮客服对话。"""

    try:
        return await process_query(
            user_query=request.query,
            session_id=request.session_id,
        )
    except ValueError as e:
        logger.warning("对话请求参数无效", error=str(e))
        raise HTTPException(status_code=422, detail=str(e)) from e
    except openai.APIError as e:
        logger.error("对话服务模型调用失败", error=str(e))
        raise HTTPException(status_code=503, detail="模型服务暂时不可用，请稍后重试") from e
