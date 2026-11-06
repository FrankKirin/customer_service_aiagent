"""
说明书检索工具
从产品说明书知识库中检索相关使用说明文档
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.tools import Tool
from langchain_core.documents import Document

from ..state import SourceInfo
from core.logger import logger
from rag.vector_store import get_vector_store_manager


class ManualRetrieverToolSchema(BaseModel):
    """说明书检索工具参数模式"""
    query: str = Field(description="用户查询的产品使用问题")
    product_model: Optional[str] = Field(default=None, description="产品型号")


def create_manual_retriever_tool() -> Tool:
    """创建说明书检索工具"""
    
    def _run(**kwargs) -> List[Document]:
        """同步执行说明书检索"""
        try:
            query = kwargs.get("query", "")
            product_model = kwargs.get("product_model")
            top_k = kwargs.get("top_k", 5)
            
            if not query:
                raise ValueError("查询文本不能为空")
            
            logger.info("检索说明书文档", query=query, product_model=product_model, top_k=top_k)
            
            # 构建筛选条件
            filter_conditions: Dict[str, Any] = {}
            if product_model:
                filter_conditions["product_model"] = product_model
            
            # 从向量数据库检索实际数据
            vector_store = get_vector_store_manager()
            documents = vector_store.search_manual_documents(
                query=query,
                filter_conditions=filter_conditions if filter_conditions else None,
                top_k=top_k
            )
            
            if not documents:
                logger.warning("未找到相关说明书文档")
                return []
            
            logger.info("说明书文档检索成功", count=len(documents))
            
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
        """异步执行说明书检索"""
        return _run(**kwargs)
    
    def _add_source_info(documents: List[Document]) -> List[Document]:
        """为文档添加来源信息"""
        for doc in documents:
            metadata = doc.metadata
            if "source_info" not in metadata:
                try:
                    source_info = SourceInfo(
                        source_id=metadata.get("source", metadata.get("doc_id", "unknown")),
                        source_type="manual",
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
        name="retrieve_manual_documents",
        description="从产品说明书知识库中检索相关使用说明文档",
        func=_run,
        coroutine=_arun,
        args_schema=ManualRetrieverToolSchema
    )