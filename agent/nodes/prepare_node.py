"""每轮开始前的状态整理。"""

from langchain_core.messages import HumanMessage

from ..state import OrchestratorState


def prepare_turn_node(state: OrchestratorState) -> dict:
    """清理上一轮临时结果，并追加当前用户消息。"""

    user_query = state.user_query.strip()
    if not user_query:
        raise ValueError("用户问题不能为空")

    # clarified_info 跨轮保留；needs_clarification 也要保留给 router，
    # 让模型知道用户这句话是在回答上一轮的澄清问题。
    return {
        "user_query": user_query,
        "query_type": "general",  # 默认type
        "missing_info": [],
        "needs_clarification": state.needs_clarification,
        "clarification_question": "",
        "final_answer": "",
        "source_citations": [],
        "should_escalate": False,
        # operator.add reducer 会追加，这里只放本轮新消息，不能放完整历史。
        "messages": [HumanMessage(content=user_query)],
    }
