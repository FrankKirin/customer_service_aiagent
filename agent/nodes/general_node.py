"""通用问答节点。"""

import openai
from langchain_core.messages import HumanMessage, SystemMessage

from core.logger import logger
from llm.base_llm import llm

from ..prompts.general_prompt import GENERAL_SYSTEM_PROMPT
from ..state import OrchestratorState


async def general_node(state: OrchestratorState) -> dict:
    """处理与售后政策、说明书都无关的普通问题。"""

    if not state.query.strip():
        raise ValueError("用户问题不能为空")

    try:
        response = await llm.ainvoke(
            [
                SystemMessage(content=GENERAL_SYSTEM_PROMPT),
                HumanMessage(content=state.query),
            ]
        )
        content = response.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError("通用回答模型返回内容为空")
    except (openai.APIError, ValueError) as e:
        logger.error("通用回答失败", query=state.query, error=str(e))
        return {
            "response": "抱歉，回答服务暂时不可用，请稍后重试。",
            "query_type": "general",
            "source_citations": [],
            "clarification_needed": False,
            "clarification_question": "",
            "should_escalate": True,
        }

    return {
        "response": content.strip(),
        "query_type": "general",
        "source_citations": [],
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": False,
    }
