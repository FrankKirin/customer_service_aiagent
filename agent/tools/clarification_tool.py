"""
信息澄清工具
当查询缺少关键信息时，向用户提问澄清
"""

from typing import List
from pydantic import BaseModel, Field
from langchain_core.tools import Tool

from core.logger import logger


class ClarificationToolSchema(BaseModel):
    """信息澄清工具参数模式"""
    missing_fields: List[str] = Field(description="缺失的信息字段列表")
    context: str = Field(description="当前对话上下文")


def create_clarification_tool() -> Tool:
    """创建信息澄清工具"""
    
    field_prompts = {
        "product_model": "请问您的产品具体型号是什么？",
        "product_line": "请问您的产品属于哪个产品线？", 
        "region": "请问您所在的地区是？",
        "issue_type": "请问您遇到的是什么类型的问题？",
    }
    
    def _run(**kwargs) -> str:
        """同步执行信息澄清"""
        try:
            missing_fields = kwargs.get("missing_fields", [])
            
            if not missing_fields:
                raise ValueError("缺失信息字段列表不能为空")
            
            logger.info("生成澄清问题", missing_fields=missing_fields)
            
            if missing_fields:
                field = missing_fields[0]
                question = field_prompts.get(field, f"请问{field}是什么？")
                return question
            else:
                return "请提供更多信息以便为您提供帮助。"
                
        except Exception as e:
            logger.error("生成澄清问题失败", error=str(e))
            return "请提供更多信息以便为您提供帮助。"
    
    async def _arun(**kwargs) -> str:
        """异步执行信息澄清"""
        return _run(**kwargs)
    
    return Tool(
        name="clarify_missing_info",
        description="当查询缺少关键信息时，向用户提问澄清",
        func=_run,
        coroutine=_arun,
        args_schema=ClarificationToolSchema
    )