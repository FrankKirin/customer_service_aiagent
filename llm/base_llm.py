# 流式传输
from langchain_openai import ChatOpenAI
from settings import get_settings
from fastapi import HTTPException
from core.logger import logger
from collections.abc import AsyncGenerator
from typing import Literal

settings = get_settings()

def get_llm(mode: str = "normal"):
    client = ChatOpenAI(
            model=settings.LLM_MODEL,
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            timeout=settings.LLM_TIME_OUT,
            max_retries=settings.MAX_RETRY_TIME,
        )
    if mode == "normal":
        return client
    elif mode == "json":
        json_client = client.bind(response_format={"type": "json_object"})
        return json_client

# 避免在node节点中反复创建llm
llm = get_llm("normal")
json_llm = get_llm("json")


def get_embedding():
    pass

async def chat_normal(query: str):
    llm = get_llm()

    try:
        result = await llm.ainvoke(query) # ainvoke是异步函数，返回一个“协程”，必须用await等待并取出结果
    except Exception as e:
        logger.exception("LLM调用失败")
        print(f"调用失败{e}")

    print(result.content)

async def chat_stream(query: str) -> AsyncGenerator:
    llm = get_llm()

    try:
        async for chunk in llm.astream(query):
            if chunk.content:
                yield chunk.content
    except Exception as e:
        logger.exception("llm_stream_failed")
        raise

if __name__ == "__main__":
    import asyncio
    query = "你是什么模型，具体的名字叫什么, 输出JSON给我"
    json_llm = get_llm("json")
    print(json_llm.invoke(query).content)
