"""
政策检索工具
从售后政策知识库中检索相关政策文档
"""

from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from langchain_core.tools import Tool
from langchain_core.documents import Document

from ..state import SourceInfo
from core.logger import logger
from rag.vector_store import get_vector_store_manager


class PolicyRetrieverToolSchema(BaseModel):
    """政策检索工具参数模式"""
    query: str = Field(description="用户查询的政策问题")
    product_line: Optional[str] = Field(default=None, description="产品线过滤")
    region: Optional[str] = Field(default=None, description="地区过滤")
    date_range: Optional[Tuple[datetime, datetime]] = Field(default=None, description="时间范围")
    # top_k为什么这么设置，理由是什么？
    top_k: int = Field(default=5, ge=1, le=20, description="返回文档数量")


def create_policy_retriever_tool() -> Tool:
    """创建政策检索工具"""
    
    def _run(**kwargs) -> List[Document]:
        """同步执行政策检索"""
        try:
            query = kwargs.get("query", "")
            product_line = kwargs.get("product_line")
            region = kwargs.get("region")
            date_range = kwargs.get("date_range")
            top_k = kwargs.get("top_k", 5)
            
            if not query:
                raise ValueError("查询文本不能为空")
            
            logger.info("检索政策文档", query=query, product_line=product_line, region=region, top_k=top_k)
            
            # 构建筛选条件
            filter_conditions: Dict[str, Any] = {}
            if product_line:
                filter_conditions["product_line"] = product_line
            if region:
                filter_conditions["region"] = region
            if date_range:
                filter_conditions["effective_date"] = {
                    "$gte": date_range[0].isoformat(),
                    "$lte": date_range[1].isoformat()
                }
            
            # 从向量数据库检索实际数据
            vector_store = get_vector_store_manager()
            documents = vector_store.search_policy_documents(
                query=query,
                filter_conditions=filter_conditions if filter_conditions else None,
                top_k=top_k
            )
            
            if not documents:
                logger.warning("未找到相关政策文档")
                return []
            
            logger.info("政策文档检索成功", count=len(documents))
            
            # 为文档添加来源信息
            documents = _add_source_info(documents)
            
            return documents
            
        except ValueError as e:
            logger.error("查询参数无效", error=str(e))
            raise
        except ConnectionError as e:
            logger.error("向量数据库连接失败", error=str(e))
            raise
        except RuntimeError as e:
            logger.error("向量检索执行失败", error=str(e))
            raise
    
    async def _arun(**kwargs) -> List[Document]:
        """异步执行政策检索"""
        return _run(**kwargs)
    
    def _add_source_info(documents: List[Document]) -> List[Document]:
        """为文档添加来源信息"""
        for doc in documents:
            metadata = doc.metadata
            if "source_info" not in metadata:
                try:
                    source_info = SourceInfo(
                        source_id=metadata.get("source", metadata.get("doc_id", "unknown")),
                        source_type="policy",
                        section=metadata.get("section"),
                        page=metadata.get("page"),
                        url=metadata.get("url"),
                        relevance_score=metadata.get("relevance_score", metadata.get("similarity_score"))
                    )
                    metadata["source_info"] = source_info
                except ValueError as e:
                    logger.warning("创建SourceInfo失败", error=str(e))
        
        return documents
    
    return Tool(
        name="retrieve_policy_documents",
        description="从售后政策知识库中检索相关政策文档",  # 给llm的提示词
        func=_run,  # agent以同步方式运行AgentExecutor.invoke(...), chain.invoke(...), langchain调用_run
        coroutine=_arun, # 异步执行入口
        args_schema=PolicyRetrieverToolSchema
    )


# 简化的类形式用于导入
PolicyRetrieverTool = create_policy_retriever_tool()


def to_source_info(doc: Document) -> Optional[SourceInfo]:
    """从Document对象中提取SourceInfo"""
    metadata = doc.metadata
    
    if "source_info" in metadata and isinstance(metadata["source_info"], SourceInfo):
        return metadata["source_info"]
    
    try:
        return SourceInfo(
            source_id=metadata.get("source", "unknown"),
            source_type="policy",
            section=metadata.get("section"),
            page=metadata.get("page"),
            url=metadata.get("url"),
            relevance_score=metadata.get("relevance_score", 0.0)
        )
    except Exception as e:
        logger.warning("解析SourceInfo失败", error=str(e))
        return None