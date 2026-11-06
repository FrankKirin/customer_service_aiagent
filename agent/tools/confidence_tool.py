"""
置信度评估工具
评估回答的总体置信度

约定（见 .clinerules）:
- 不使用任何自造的模拟分数: 只用文档元数据里真实存在的 relevance_score,
  没有检索结果时置信度为 0, 需要上层给出校验分时通过 validation_score 显式传入
- 异常精确捕获(ValueError / TypeError)
- _arun 不调用 _run, 异步入口独立实现
"""

from typing import Dict, List, Optional, Union

from langchain_core.documents import Document
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from core.logger import logger

# 检索分数与校验分数的加权系数(业务规则: 检索相关性权重更高)
RETRIEVAL_WEIGHT = 0.7
VALIDATION_WEIGHT = 0.3

# 低于该阈值时建议走兜底(转人工/提示补充信息)
FALLBACK_THRESHOLD = 0.4


class ConfidenceAssessmentToolSchema(BaseModel):
    """置信度评估工具参数模式"""

    query: str = Field(description="用户查询")
    retrieved_docs: List[Document] = Field(description="检索到的文档")
    validation_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="上层节点给出的答案校验分数(0-1), 未提供时只用检索相关度评估",
    )


def _calculate_retrieval_score(documents: List[Document]) -> float:
    """计算检索相关性分数: 取所有文档 relevance_score 的平均值

    文档元数据里没有 relevance_score 或数值非法时跳过, 不做任何填充假设。
    """
    scores: List[float] = []

    for doc in documents:
        raw_value = doc.metadata.get("relevance_score")
        if raw_value is None:
            continue
        try:
            score = float(raw_value)
        except (TypeError, ValueError):
            logger.warning("检索相关度不是有效数值, 已忽略", raw_value=str(raw_value))
            continue
        if 0.0 <= score <= 1.0:
            scores.append(score)
        else:
            logger.warning("检索相关度超出0-1范围, 已忽略", raw_value=str(raw_value))

    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def _assess_confidence(
    query: str,
    retrieved_docs: List[Document],
    validation_score: Optional[float],
) -> Dict[str, Union[float, int, bool, None]]:
    """计算总体置信度(纯内存计算, 无阻塞IO)"""
    if not query:
        raise ValueError("查询文本不能为空")

    retrieval_score = _calculate_retrieval_score(retrieved_docs)

    if validation_score is None:
        # 没有校验分时, 只用真实的检索相关度, 不引入任何假设值
        overall = retrieval_score
    else:
        overall = retrieval_score * RETRIEVAL_WEIGHT + validation_score * VALIDATION_WEIGHT

    overall = min(max(overall, 0.0), 1.0)

    return {
        "overall_confidence": round(overall, 4),
        "retrieval_score": retrieval_score,
        "validation_score": validation_score,
        "doc_count": len(retrieved_docs),
        "should_fallback": overall < FALLBACK_THRESHOLD,
    }


def create_confidence_tool() -> StructuredTool:
    """创建置信度评估工具"""

    def _run(**kwargs) -> Dict[str, Union[float, int, bool, None]]:
        """同步执行置信度评估"""
        result = _assess_confidence(
            query=kwargs.get("query", ""),
            retrieved_docs=kwargs.get("retrieved_docs", []),
            validation_score=kwargs.get("validation_score"),
        )
        logger.info(
            "置信度评估完成",
            overall_confidence=result["overall_confidence"],
            retrieval_score=result["retrieval_score"],
            doc_count=result["doc_count"],
            should_fallback=result["should_fallback"],
        )
        return result

    async def _arun(**kwargs) -> Dict[str, Union[float, int, bool, None]]:
        """异步执行置信度评估(无IO操作, 直接返回计算结果)"""
        result = _assess_confidence(
            query=kwargs.get("query", ""),
            retrieved_docs=kwargs.get("retrieved_docs", []),
            validation_score=kwargs.get("validation_score"),
        )
        logger.info(
            "置信度评估完成(异步)",
            overall_confidence=result["overall_confidence"],
            retrieval_score=result["retrieval_score"],
            doc_count=result["doc_count"],
            should_fallback=result["should_fallback"],
        )
        return result

    # 多参数工具必须用 StructuredTool, 单参数 Tool 只接受一个入参
    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="assess_confidence",
        description="评估回答的总体置信度",
        args_schema=ConfidenceAssessmentToolSchema,
    )


# 简化的实例形式用于导入
ConfidenceTool = create_confidence_tool()
