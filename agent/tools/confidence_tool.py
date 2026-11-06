"""
置信度评估工具
评估回答的总体置信度
"""

from typing import List, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import Tool
from langchain_core.documents import Document

from core.logger import logger


class ConfidenceAssessmentToolSchema(BaseModel):
    """置信度评估工具参数模式"""
    query: str = Field(description="用户查询")
    retrieved_docs: List[Document] = Field(description="检索到的文档")


def create_confidence_tool() -> Tool:
    """创建置信度评估工具"""
    
    def _run(**kwargs) -> dict:
        """同步执行置信度评估"""
        try:
            query = kwargs.get("query", "")
            retrieved_docs = kwargs.get("retrieved_docs", [])
            
            if not query:
                raise ValueError("查询文本不能为空")
            
            retrieval_score = _calculate_retrieval_score(retrieved_docs)
            validation_score = 0.5
            
            # 总体置信度=检索置信度分数*0.6 + 验证置信度分数*0.3
            overall = retrieval_score * 0.6 + validation_score * 0.3 + 0.1
            
            return {
                "overall_confidence": min(overall, 1.0),
                "retrieval_score": retrieval_score,
                "validation_score": validation_score,
                "should_fallback": overall < 0.4
            }
            
        except Exception as e:
            logger.error("置信度评估失败", error=str(e))
            return {
                "overall_confidence": 0.3,
                "retrieval_score": 0.3,
                "validation_score": 0.3,
                "should_fallback": True
            }
    
    async def _arun(**kwargs) -> dict:
        """异步执行置信度评估"""
        return _run(**kwargs)
    
    def _calculate_retrieval_score(docs: List[Document]) -> float:
        """计算检索相关性分数"""
        if not docs:
            return 0.0
        
        scores = []
        for doc in docs:
            metadata = doc.metadata
            if "relevance_score" in metadata:
                try:
                    score = float(metadata["relevance_score"])
                    if 0 <= score <= 1:
                        scores.append(score)
                except (ValueError, TypeError):
                    pass
        
        if scores:
            return sum(scores) / len(scores)
        return 0.5
    
    return Tool(
        name="assess_confidence",
        description="评估回答的总体置信度",
        func=_run,
        coroutine=_arun,
        args_schema=ConfidenceAssessmentToolSchema
    )