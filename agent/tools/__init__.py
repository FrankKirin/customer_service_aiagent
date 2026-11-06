"""
工具模块
包含所有Agent使用的工具函数
"""

from .policy_retriever import create_policy_retriever_tool
from .manual_retriever import create_manual_retriever_tool
from .clarification_tool import create_clarification_tool
from .confidence_tool import create_confidence_tool
from typing import Optional

# 创建工具实例
PolicyRetrieverTool = create_policy_retriever_tool()
ManualRetrieverTool = create_manual_retriever_tool()
ClarificationTool = create_clarification_tool()  
ConfidenceTool = create_confidence_tool()

__all__ = [
    "PolicyRetrieverTool",
    "ManualRetrieverTool", 
    "ClarificationTool",
    "ConfidenceTool",
]