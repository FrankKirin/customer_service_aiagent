"""主图路由节点：规则优先，LLM 兜底。"""

from typing import Any, Dict, List, Literal, Optional

import openai
from pydantic import BaseModel, Field, ValidationError

from core.logger import logger
from llm.base_llm import json_llm

from ..prompts.router_prompt import build_router_prompt
from ..routing_rules import (
    RuleRoutingResult,
    classify_by_rules,
    extract_slots,
    looks_ambiguous,
)
from ..state import OrchestratorState
from ..utils.context import build_router_history
from ..utils.json_parser import parse_json_model

LLM_MIN_CONFIDENCE = 0.5


class RouterLLMOutput(BaseModel):
    """路由模型的结构化输出。"""

    query_type: Literal["policy", "manual", "general"]
    confidence: float = Field(ge=0.0, le=1.0)
    missing_info: List[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str = ""
    clarified_info: Dict[str, Any] = Field(default_factory=dict)
    product_line: Optional[str] = None
    region: Optional[str] = None
    product_model: Optional[str] = None
    issue_type: Optional[Literal["operation", "maintenance", "troubleshooting"]] = None


def _collect_llm_slots(output: RouterLLMOutput) -> Dict[str, Any]:
    """收集模型输出中的槽位，兼容 clarified_info 和顶层字段。"""

    slots = {
        key: value
        for key, value in output.clarified_info.items()
        if value not in (None, "")
    }
    for field_name in ("product_line", "region", "product_model", "issue_type"):
        value = getattr(output, field_name)
        if value:
            slots[field_name] = value
    return slots


def _build_route_update(
    state: OrchestratorState,
    *,
    query_type: Literal["policy", "manual", "general"],
    needs_clarification: bool = False,
    missing_info: Optional[List[str]] = None,
    clarification_question: str = "",
    llm_output: Optional[RouterLLMOutput] = None,
) -> Dict[str, Any]:
    """统一构造路由节点的 State 更新。"""

    clarified_info = dict(state.clarified_info)
    if llm_output is not None:
        clarified_info.update(_collect_llm_slots(llm_output))
    clarified_info = extract_slots(state.user_query, clarified_info)

    if needs_clarification:
        final_missing_info = list(missing_info or ["query_scope"])
        final_question = clarification_question
    else:
        final_missing_info = []
        final_question = ""

    return {
        "query_type": query_type,
        "missing_info": final_missing_info,
        "needs_clarification": needs_clarification,
        "clarification_question": final_question,
        "should_escalate": False,
        "clarified_info": clarified_info,
    }


def _should_force_clarification(
    state: OrchestratorState,
    output: RouterLLMOutput,
) -> bool:
    """判断是否必须忽略模型结论，先向用户确认问题对象。"""

    if not looks_ambiguous(state.user_query):
        return False

    # 低置信度且存在指代时，宁可追问一次，也不要把问题硬塞给某个子图。
    if output.confidence < LLM_MIN_CONFIDENCE:
        return True

    # 首轮就出现“这个/那个”且没有历史可借，模型即使很自信也应先确认对象。
    if state.needs_clarification:
        return False

    return not build_router_history(state.messages, state.user_query)


def _fallback_update(
    state: OrchestratorState,
    rule: Optional[RuleRoutingResult],
) -> Dict[str, Any]:
    """LLM 不可用时，只在规则或明显歧义上做保守处理。"""

    if rule is not None:
        return _build_route_update(state, query_type=rule.query_type)

    if looks_ambiguous(state.user_query):
        return _build_route_update(
            state,
            query_type="general",
            needs_clarification=True,
        )

    return _build_route_update(state, query_type="general")


def _apply_llm_output(
    state: OrchestratorState,
    output: RouterLLMOutput,
) -> Dict[str, Any]:
    needs_clarification = (
        output.needs_clarification
        or _should_force_clarification(state, output)
    )
    return _build_route_update(
        state,
        query_type=output.query_type,
        needs_clarification=needs_clarification,
        missing_info=output.missing_info,
        clarification_question=output.clarification_question,
        llm_output=output,
    )


async def _call_router_llm(state: OrchestratorState) -> RouterLLMOutput:
    prompt = build_router_prompt(
        query=state.user_query,
        history=build_router_history(state.messages, state.user_query),
        clarification_pending=state.needs_clarification,
        clarified_info=state.clarified_info,
    )
    response = await json_llm.ainvoke(prompt)
    content = response.content
    if not isinstance(content, str):
        raise ValueError("路由模型返回内容不是文本")
    return parse_json_model(content, RouterLLMOutput)


async def router_node(state: OrchestratorState) -> dict:
    """先走规则，规则无法判断时调用 LLM。"""

    rule_result = classify_by_rules(state.user_query)

    # 明显单向命中且没有等待澄清时，直接使用规则，避免无意义 LLM 调用。
    if rule_result is not None and not state.needs_clarification:
        logger.info("规则路由命中", query=state.user_query, rule=rule_result.model_dump())
        return _build_route_update(state, query_type=rule_result.query_type)

    # 等待澄清或多义词必须让模型结合历史判断。
    try:
        llm_output = await _call_router_llm(state)
    except (openai.APIError, ValueError, ValidationError, TypeError) as e:
        logger.warning("路由模型失败，使用保守兜底", query=state.user_query, error=str(e))
        return _fallback_update(state, rule_result)

    logger.info("LLM 路由完成", query=state.user_query, output=llm_output.model_dump())
    return _apply_llm_output(state, llm_output)
