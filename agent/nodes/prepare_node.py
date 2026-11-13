"""每轮开始前的状态整理。"""

from datetime import datetime

from langchain_core.messages import HumanMessage

from ..state import OrchestratorState


def prepare_turn_node(state: OrchestratorState) -> dict:
    """清理上一轮临时结果，并追加当前用户消息。"""

    query = state.query.strip()
    if not query:
        raise ValueError("用户问题不能为空")

    # clarification_needed 不能在这里直接清空：
    # 路由节点需要知道上一轮是否正在等待用户补充信息。
    return {
        "query": query,
        "query_type": "general",
        "confidence": 0.0,
        "missing_info": [],
        # 显式保留：router 需要据此决定是否结合上一轮澄清回答。
        "clarification_needed": state.clarification_needed,
        "clarification_question": "",
        "retrieved_docs": [],
        "source_info": [],
        "retrieval_confidence": 0.0,
        "response": "",
        "source_citations": [],
        "should_escalate": False,
        "timestamp": datetime.now(),
        "processing_time_ms": 0.0,
        # 领域槽位由 router 从当前问题和 clarified_info 重新填充。
        "product_line": None,
        "region": None,
        "product_model": None,
        "product_version": None,
        "issue_type": None,
        # 领域结果每轮重置，避免上一轮政策结论污染下一轮说明书回答。
        "policy_applicability": "unknown",
        "policy_conditions": {},
        "policy_summary": "",
        "policy_violations": [],
        "step_by_step_guide": False,
        "safety_warnings": [],
        "tools_needed": [],
        "requires_expert": False,
        "complexity_level": "low",
        # operator.add reducer 会追加，这里只放本轮新消息，不能放完整历史。
        "messages": [HumanMessage(content=query)],
    }
