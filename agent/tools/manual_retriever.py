"""
说明书检索工具
从产品说明书知识库中检索相关使用说明文档

约定（见 .clinerules）:
- 不返回任何自造的模拟数据, 检索不到就返回空列表
- 异常精确捕获(ValueError / ChromaError / openai.APIError)
- 提供真异步实现: _arun 直接 await 向量库的异步检索, 不调用 _run
"""

from typing import Any, Dict, List, Optional

import chromadb
import openai
from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError

from core.logger import logger
from rag.vector_store import get_vector_store_manager

from ..state import SourceInfo


class ManualRetrieverToolSchema(BaseModel):
    """说明书检索工具参数模式"""

    query: str = Field(description="用户查询的产品使用问题")
    product_model: Optional[str] = Field(default=None, description="产品型号过滤")
    issue_type: Optional[str] = Field(
        default=None, description="问题类型过滤: operation(操作) / maintenance(维护) / troubleshooting(故障排查)"
    )
    top_k: int = Field(default=5, ge=1, le=20, description="返回文档数量")


def _build_filter_conditions(
    product_model: Optional[str],
    issue_type: Optional[str],
) -> Optional[Dict[str, Any]]:
    """构建 Chroma 的 where 过滤条件

    只把用户真实提供的维度写进过滤条件, 没有任何过滤维度时返回 None。
    注意: 过滤字段必须在文档元数据里真实存在, 否则 Chroma 会返回空结果。
    """
    conditions: Dict[str, Any] = {}

    if product_model:
        conditions["product_model"] = product_model
    if issue_type:
        conditions["issue_type"] = issue_type

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
                source_type="manual",
                section=metadata.get("section"),
                page=metadata.get("page"),
                url=metadata.get("url") or metadata.get("source_url"),
                relevance_score=float(metadata.get("relevance_score") or 0.0),
            )
        except (ValidationError, TypeError, ValueError) as e:
            # 单个文档的元数据异常不应影响其他检索结果
            logger.warning("创建SourceInfo失败", source=str(metadata.get("source")), error=str(e))
    return documents


def create_manual_retriever_tool() -> StructuredTool:
    """创建说明书检索工具"""

    def _run(**kwargs) -> List[Document]:
        """同步执行说明书检索"""
        query = kwargs.get("query", "")
        if not query:
            raise ValueError("查询文本不能为空")

        top_k = kwargs.get("top_k", 5)
        filter_conditions = _build_filter_conditions(
            product_model=kwargs.get("product_model"),
            issue_type=kwargs.get("issue_type"),
        )
        logger.info("检索说明书文档", query=query, filter_conditions=filter_conditions, top_k=top_k)

        try:
            documents = get_vector_store_manager().search_manual_documents(
                query=query,
                filter_conditions=filter_conditions,
                top_k=top_k,
            )
            if not documents and kwargs.get("issue_type"):
                # 当前说明书元数据不一定都有 issue_type，过滤为空时回退到
                # 只保留 product_model 的检索，避免把真实可召回文档全部过滤掉。
                fallback_conditions = _build_filter_conditions(
                    product_model=kwargs.get("product_model"),
                    issue_type=None,
                )
                logger.warning(
                    "按issue_type过滤无结果, 回退到不带该条件的检索",
                    query=query,
                    issue_type=kwargs.get("issue_type"),
                )
                documents = get_vector_store_manager().search_manual_documents(
                    query=query,
                    filter_conditions=fallback_conditions,
                    top_k=top_k,
                )
        except chromadb.errors.ChromaError as e:
            logger.error("说明书向量库检索失败", query=query, error=str(e))
            raise
        except openai.APIError as e:
            logger.error("硅基流动embedding接口调用失败", query=query, error=str(e))
            raise

        if not documents:
            logger.warning("未找到相关说明书文档", query=query)
            return []

        logger.info("说明书文档检索成功", count=len(documents))
        return _attach_source_info(documents)

    async def _arun(**kwargs) -> List[Document]:
        """异步执行说明书检索(真异步: await 向量库的异步检索接口)"""
        query = kwargs.get("query", "")
        if not query:
            raise ValueError("查询文本不能为空")

        top_k = kwargs.get("top_k", 5)
        filter_conditions = _build_filter_conditions(
            product_model=kwargs.get("product_model"),
            issue_type=kwargs.get("issue_type"),
        )
        logger.info("检索说明书文档(异步)", query=query, filter_conditions=filter_conditions, top_k=top_k)

        try:
            documents = await get_vector_store_manager().asearch_manual_documents(
                query=query,
                filter_conditions=filter_conditions,
                top_k=top_k,
            )
            if not documents and kwargs.get("issue_type"):
                # 与同步入口保持一致：元数据缺 issue_type 时不能把结果全部过滤掉。
                fallback_conditions = _build_filter_conditions(
                    product_model=kwargs.get("product_model"),
                    issue_type=None,
                )
                logger.warning(
                    "按issue_type过滤无结果(异步), 回退到不带该条件的检索",
                    query=query,
                    issue_type=kwargs.get("issue_type"),
                )
                documents = await get_vector_store_manager().asearch_manual_documents(
                    query=query,
                    filter_conditions=fallback_conditions,
                    top_k=top_k,
                )
        except chromadb.errors.ChromaError as e:
            logger.error("说明书向量库检索失败(异步)", query=query, error=str(e))
            raise
        except openai.APIError as e:
            logger.error("硅基流动embedding接口调用失败(异步)", query=query, error=str(e))
            raise

        if not documents:
            logger.warning("未找到相关说明书文档(异步)", query=query)
            return []

        logger.info("说明书文档检索成功(异步)", count=len(documents))
        return _attach_source_info(documents)

    # 多参数工具必须用 StructuredTool, 单参数 Tool 只接受一个入参
    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="retrieve_manual_documents",
        description="从产品说明书知识库中检索相关使用说明文档",
        args_schema=ManualRetrieverToolSchema,
    )


# 简化的实例形式用于导入
ManualRetrieverTool = create_manual_retriever_tool()
