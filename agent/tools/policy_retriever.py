"""
政策检索工具
从售后政策知识库中检索相关政策文档

约定（见 .clinerules）:
- 不返回任何自造的模拟数据, 检索不到就返回空列表
- 异常精确捕获(ValueError / ChromaError / openai.APIError)
- 提供真异步实现: _arun 直接 await 向量库的异步检索, 不调用 _run
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import chromadb
import openai
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from core.logger import logger
from rag.vector_store import get_vector_store_manager

from ..state import SourceInfo


class PolicyRetrieverToolSchema(BaseModel):
    """政策检索工具参数模式"""

    query: str = Field(description="用户查询的政策问题")
    product_line: Optional[str] = Field(default=None, description="产品线过滤")
    region: Optional[str] = Field(default=None, description="地区过滤")
    date_range: Optional[Tuple[datetime, datetime]] = Field(
        default=None, description="政策生效时间范围:(开始时间, 结束时间)"
    )
    # 默认5条: 政策条款通常按"条款"组织, 5条覆盖一次问询的主要条款且不超出上下文预算
    top_k: int = Field(default=5, ge=1, le=20, description="返回文档数量")


def _build_filter_conditions(
    product_line: Optional[str],
    region: Optional[str],
    date_range: Optional[Tuple[datetime, datetime]],
) -> Optional[Dict[str, Any]]:
    """构建 Chroma 的 where 过滤条件

    只把用户真实提供的维度写进过滤条件, 没有任何过滤维度时返回 None。
    注意: 过滤字段必须在文档元数据里真实存在, 否则 Chroma 会返回空结果。
    """
    conditions: Dict[str, Any] = {}

    if product_line:
        conditions["product_line"] = product_line
    if region:
        conditions["region"] = region
    if date_range is not None:
        if len(date_range) != 2:
            raise ValueError("date_range 必须包含开始时间和结束时间两个元素")
        start_date, end_date = date_range
        if start_date > end_date:
            raise ValueError("date_range 的开始时间不能晚于结束时间")
        conditions["effective_date"] = {
            "$gte": start_date.date().isoformat(),
            "$lte": end_date.date().isoformat(),
        }

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions
    # Chroma 的多条件必须用 $and 显式组合
    return {"$and": [{key: value} for key, value in conditions.items()]}


def _attach_source_info(documents: List[Document]) -> List[Document]:
    """为文档补充来源信息(写回 metadata 的 source_info)"""
    for doc in documents:
        metadata = doc.metadata
        if "source_info" in metadata:
            continue
        try:
            metadata["source_info"] = SourceInfo(
                source_id=metadata.get("source") or metadata.get("doc_id") or "unknown",
                source_type="policy",
                section=metadata.get("section"),
                page=metadata.get("page"),
                url=metadata.get("url") or metadata.get("source_url"),
                relevance_score=float(metadata.get("relevance_score") or 0.0),
            )
        except (ValidationError, TypeError, ValueError) as e:
            # 单个文档的元数据异常不应影响其他检索结果
            logger.warning("创建SourceInfo失败", source=str(metadata.get("source")), error=str(e))
    return documents


def create_policy_retriever_tool() -> StructuredTool:
    """创建政策检索工具"""

    def _run(**kwargs) -> List[Document]:
        """同步执行政策检索"""
        query = kwargs.get("query", "")
        if not query:
            raise ValueError("查询文本不能为空")

        top_k = kwargs.get("top_k", 5)
        filter_conditions = _build_filter_conditions(
            product_line=kwargs.get("product_line"),
            region=kwargs.get("region"),
            date_range=kwargs.get("date_range"),
        )
        logger.info("检索政策文档", query=query, filter_conditions=filter_conditions, top_k=top_k)

        try:
            documents = get_vector_store_manager().search_policy_documents(
                query=query,
                filter_conditions=filter_conditions,
                top_k=top_k,
            )
        except chromadb.errors.ChromaError as e:
            logger.error("政策向量库检索失败", query=query, error=str(e))
            raise
        except openai.APIError as e:
            logger.error("硅基流动embedding接口调用失败", query=query, error=str(e))
            raise

        if not documents:
            logger.warning("未找到相关政策文档", query=query)
            return []

        logger.info("政策文档检索成功", count=len(documents))
        return _attach_source_info(documents)

    async def _arun(**kwargs) -> List[Document]:
        """异步执行政策检索(真异步: await 向量库的异步检索接口)"""
        query = kwargs.get("query", "")
        if not query:
            raise ValueError("查询文本不能为空")

        top_k = kwargs.get("top_k", 5)
        filter_conditions = _build_filter_conditions(
            product_line=kwargs.get("product_line"),
            region=kwargs.get("region"),
            date_range=kwargs.get("date_range"),
        )
        logger.info("检索政策文档(异步)", query=query, filter_conditions=filter_conditions, top_k=top_k)

        try:
            documents = await get_vector_store_manager().asearch_policy_documents(
                query=query,
                filter_conditions=filter_conditions,
                top_k=top_k,
            )
        except chromadb.errors.ChromaError as e:
            logger.error("政策向量库检索失败(异步)", query=query, error=str(e))
            raise
        except openai.APIError as e:
            logger.error("硅基流动embedding接口调用失败(异步)", query=query, error=str(e))
            raise

        if not documents:
            logger.warning("未找到相关政策文档(异步)", query=query)
            return []

        logger.info("政策文档检索成功(异步)", count=len(documents))
        return _attach_source_info(documents)

    # 多参数工具必须用 StructuredTool, 单参数 Tool 只接受一个入参
    return StructuredTool.from_function(
        func=_run,         # 同步入口: AgentExecutor.invoke / chain.invoke 会调用它
        coroutine=_arun,   # 异步入口: ainvoke 系列会调用它
        name="retrieve_policy_documents",
        description="从售后政策知识库中检索相关政策文档",  # 给llm的提示词
        args_schema=PolicyRetrieverToolSchema,
    )


# 简化的实例形式用于导入
PolicyRetrieverTool = create_policy_retriever_tool()


def to_source_info(doc: Document) -> Optional[SourceInfo]:
    """从Document对象中提取SourceInfo"""
    metadata = doc.metadata

    if isinstance(metadata.get("source_info"), SourceInfo):
        return metadata["source_info"]

    try:
        return SourceInfo(
            source_id=metadata.get("source") or metadata.get("doc_id") or "unknown",
            source_type="policy",
            section=metadata.get("section"),
            page=metadata.get("page"),
            url=metadata.get("url") or metadata.get("source_url"),
            relevance_score=float(metadata.get("relevance_score") or 0.0),
        )
    except (ValidationError, TypeError, ValueError) as e:
        logger.warning("解析SourceInfo失败", error=str(e))
        return None
