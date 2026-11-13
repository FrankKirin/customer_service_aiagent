"""澄清节点：只负责向用户提出一个关键补充问题。"""

from core.logger import logger

from ..state import OrchestratorState
from ..tools.clarification_tool import ClarificationTool
from ..utils.context import build_contextual_query


async def clarification_node(state: OrchestratorState) -> dict:
    """生成澄清问题并作为本轮回答返回。"""

    if state.clarification_question:
        question = state.clarification_question
    elif state.missing_info:
        question = await ClarificationTool.ainvoke(
            {
                "missing_fields": state.missing_info,
                "context": build_contextual_query(state),
            }
        )
    else:
        question = "请问您具体想咨询哪方面的问题？"

    logger.info(
        "生成澄清问题",
        query=state.query,
        missing_info=state.missing_info,
    )
    return {
        "query_type": state.query_type,
        "response": question,
        "clarification_needed": True,
        "clarification_question": question,
        "should_escalate": False,
    }
