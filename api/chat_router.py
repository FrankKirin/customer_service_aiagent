from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.base_llm import chat_stream

router = APIRouter()

@router.get("/chat")
async def chat(query: str):
    async def event_generator(): # 从LLM获取数据块，将数据格式化为SSE格式，逐块交给FastAPI发送
        async for chunk in chat_stream(query):
            yield f"data: {chunk}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-steam",
    )
