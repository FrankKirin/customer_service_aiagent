"""
信息澄清工具
当查询缺少关键信息时，向用户提问澄清

约定（见 .clinerules）:
- 异常精确捕获, 不使用裸 Exception
- _arun 不调用 _run, 异步入口独立实现
"""

from typing import Dict, List

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from core.logger import logger

# 澄清问题模板: key 对应 SharedState.missing_info 中的字段名
FIELD_PROMPTS: Dict[str, str] = {
    "query_scope": "请问您想咨询售后政策（退换/保修/收费），还是产品使用/故障排查？",
    "purchase_channel": "请问您是通过线上商城、电视、电话、邮购购买，还是线下门店购买？",
    "received_days": "请问您收到产品多长时间了？",
    "fault_type": "请问具体是什么故障或异常现象？",
    "repair_count": "请问同一种性能故障已经维修过几次？",
    "warranty_proof": "请问您是否还在三包有效期内，并能提供有效购机凭证？",
    "product_model": "请问您的产品具体型号是什么？",
    "product_line": "请问您的产品属于哪个产品线？",
    "region": "请问您所在的地区是？",
    "issue_type": "请问您遇到的是什么类型的问题？",
}


class ClarificationToolSchema(BaseModel):
    """信息澄清工具参数模式"""

    missing_fields: List[str] = Field(description="缺失的信息字段列表")
    context: str = Field(description="当前对话上下文")


def _build_question(missing_fields: List[str]) -> str:
    """根据缺失字段生成澄清问题(纯内存计算, 无阻塞IO)"""
    if not missing_fields:
        raise ValueError("缺失信息字段列表不能为空")

    field = missing_fields[0]
    return FIELD_PROMPTS.get(field, f"请问{field}是什么？")


def create_clarification_tool() -> StructuredTool:
    """创建信息澄清工具"""

    def _run(**kwargs) -> str:
        """同步执行信息澄清"""
        missing_fields = kwargs.get("missing_fields", [])
        question = _build_question(missing_fields)
        logger.info(
            "生成澄清问题",
            missing_fields=missing_fields,
            fields_total=len(missing_fields),
        )
        return question

    async def _arun(**kwargs) -> str:
        """异步执行信息澄清(无IO操作, 直接返回计算结果)"""
        missing_fields = kwargs.get("missing_fields", [])
        question = _build_question(missing_fields)
        logger.info(
            "生成澄清问题(异步)",
            missing_fields=missing_fields,
            fields_total=len(missing_fields),
        )
        return question

    # 多参数工具必须用 StructuredTool, 单参数 Tool 只接受一个入参
    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="clarify_missing_info",
        description="当查询缺少关键信息时，向用户提问澄清",
        args_schema=ClarificationToolSchema,
    )


# 简化的实例形式用于导入
ClarificationTool = create_clarification_tool()
