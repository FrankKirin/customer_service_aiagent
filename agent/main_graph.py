"""主编排图：prepare -> router -> 子图/澄清 -> finalize。"""

from functools import lru_cache
from typing import Literal, Optional

from langgraph.graph import END, START, StateGraph

from .nodes.clarification_node import clarification_node
from .nodes.finalize_node import finalize_node
from .nodes.general_node import general_node
from .nodes.prepare_node import prepare_turn_node
from .nodes.router_node import router_node
from .session import build_thread_config, get_checkpointer
from .state import AgentResponse, OrchestratorState
from .subgraphs.manual_agent import build_manual_agent
from .subgraphs.policy_agent import build_policy_agent


def route_after_router(state: OrchestratorState) -> Literal[
    "clarify", "policy_agent", "manual_agent", "general_agent"
]:
    """路由节点之后的分支选择。"""

    if state.clarification_needed:
        return "clarify"
    if state.query_type == "policy":
        return "policy_agent"
    if state.query_type == "manual":
        return "manual_agent"
    return "general_agent"


def build_main_graph():
    """构建并编译主图。"""

    workflow = StateGraph(OrchestratorState)
    workflow.add_node("prepare_turn", prepare_turn_node)
    workflow.add_node("router", router_node)
    workflow.add_node("clarify", clarification_node)
    workflow.add_node("policy_agent", build_policy_agent())
    workflow.add_node("manual_agent", build_manual_agent())
    workflow.add_node("general_agent", general_node)
    workflow.add_node("finalize", finalize_node)

    workflow.add_edge(START, "prepare_turn")
    workflow.add_edge("prepare_turn", "router")
    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {
            "clarify": "clarify",
            "policy_agent": "policy_agent",
            "manual_agent": "manual_agent",
            "general_agent": "general_agent",
        },
    )
    workflow.add_edge("clarify", "finalize")
    workflow.add_edge("policy_agent", "finalize")
    workflow.add_edge("manual_agent", "finalize")
    workflow.add_edge("general_agent", "finalize")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=get_checkpointer())


@lru_cache(maxsize=1)
# 函数只生成一次，之后每次调用直接返回第一次生成好的，编译完成的StateGraph实例
def get_main_graph():
    """编译一次，进程内复用。"""

    return build_main_graph()


async def process_query(
    query: str,
    session_id: str,
    user_id: Optional[str] = None,
) -> AgentResponse:
    """对外统一入口，供 API 调用。"""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id 不能为空")

    result = await get_main_graph().ainvoke(
        {
            "query": query.strip(),
            "session_id": session_id.strip(),
            "user_id": user_id,
        },
        build_thread_config(session_id.strip()),
    )
    state = OrchestratorState.model_validate(result)
    return AgentResponse(
        response=state.response,
        query_type=state.query_type,
        source_citations=state.source_citations,
        should_escalate=state.should_escalate,
        processing_time_ms=state.processing_time_ms,
    )
