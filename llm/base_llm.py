# 流式传输
from langchain_openai import ChatOpenAI
from settings import settings
from fastapi import HTTPException
from core.logger import logger
from collections.abc import AsyncGenerator
from typing import Literal

def get_llm(mode: str = "normal"):
    client = ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout,
            max_retries=settings.max_retry_time,
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
