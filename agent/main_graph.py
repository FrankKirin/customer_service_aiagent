"""主编排图：prepare -> router -> 子图/澄清 -> finalize。"""

import time
from functools import lru_cache
from typing import Literal

from langgraph.graph import END, START, StateGraph

from .nodes.clarification_node import clarification_node
from .nodes.finalize_node import finalize_node
from .nodes.general_node import general_node
from .nodes.prepare_node import prepare_turn_node
from .nodes.router_node import router_node
from .nodes.subgraph_wrappers import (
    build_manual_wrapper_node,
    build_policy_wrapper_node,
)
from .session import build_thread_config, get_checkpointer
from .state import AgentResponse, OrchestratorState
from .subgraphs.manual_agent import build_manual_agent
from .subgraphs.policy_agent import build_policy_agent


def route_after_router(state: OrchestratorState) -> Literal[
    "clarify", "policy_agent", "manual_agent", "general_agent"
]:
    """路由节点之后的分支选择。"""

    if state.needs_clarification:
        return "clarify"
    if state.query_type == "policy":
        return "policy_agent"
    if state.query_type == "manual":
        return "manual_agent"
    return "general_agent"


def route_after_subgraph(state: OrchestratorState) -> Literal["clarify", "finalize"]:
    """领域子图要求补充信息时，统一交给父图澄清节点出口。"""

    if state.needs_clarification:
        return "clarify"
    return "finalize"


def build_main_graph():
    """构建并编译主图。"""

    policy_graph = build_policy_agent()
    manual_graph = build_manual_agent()

    workflow = StateGraph(OrchestratorState)
    workflow.add_node("prepare_turn", prepare_turn_node)
    workflow.add_node("router", router_node)
    workflow.add_node("clarify", clarification_node)
    workflow.add_node("policy_agent", build_policy_wrapper_node(policy_graph))
    workflow.add_node("manual_agent", build_manual_wrapper_node(manual_graph))
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
    for agent_node in ("policy_agent", "manual_agent"):
        workflow.add_conditional_edges(
            agent_node,
            route_after_subgraph,
            {
                "clarify": "clarify",
                "finalize": "finalize",
            },
        )
    workflow.add_edge("general_agent", "finalize")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=get_checkpointer())


@lru_cache(maxsize=1)
def get_main_graph():
    """编译一次，进程内复用。"""

    return build_main_graph()


async def process_query(
    user_query: str,
    session_id: str,
) -> AgentResponse:
    """对外统一入口，供 API 调用。"""

    if not isinstance(user_query, str) or not user_query.strip():
        raise ValueError("user_query 不能为空")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id 不能为空")

    started_at = time.perf_counter()
    result = await get_main_graph().ainvoke(
        {"user_query": user_query.strip()},
        build_thread_config(session_id.strip()),
    )
    processing_time_ms = round((time.perf_counter() - started_at) * 1000, 2)

    state = OrchestratorState.model_validate(result)
    return AgentResponse(
        response=state.final_answer,
        query_type=state.query_type,
        source_citations=state.source_citations,
        should_escalate=state.should_escalate,
        needs_clarification=state.needs_clarification,
        clarification_question=state.clarification_question,
        processing_time_ms=processing_time_ms,
    )
