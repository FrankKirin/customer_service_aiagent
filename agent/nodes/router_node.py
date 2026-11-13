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


def _merged_slots(state: OrchestratorState, llm_slots: Dict[str, Any]) -> Dict[str, Any]:
    clarified_info = dict(state.clarified_info)
    clarified_info.update(llm_slots)
    return extract_slots(state.query, clarified_info)


def _slot_update(
    state: OrchestratorState,
    llm_output: Optional[RouterLLMOutput] = None,
) -> Dict[str, Any]:
    clarified_info = _merged_slots(state, llm_output.clarified_info if llm_output else {})
    return {
        "clarified_info": clarified_info,
        "product_line": (
            (llm_output.product_line if llm_output else None)
            or clarified_info.get("product_line")
        ),
        "region": (
            (llm_output.region if llm_output else None)
            or clarified_info.get("region")
        ),
        "product_model": (
            (llm_output.product_model if llm_output else None)
            or clarified_info.get("product_model")
        ),
        "product_version": clarified_info.get("product_version"),
        "issue_type": (
            (llm_output.issue_type if llm_output else None)
            or clarified_info.get("issue_type")
        ),
    }


def _rule_update(state: OrchestratorState, rule: RuleRoutingResult) -> Dict[str, Any]:
    return {
        "query_type": rule.query_type,
        "confidence": rule.confidence,
        "missing_info": [],
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": False,
        **_slot_update(state),
    }


def _clarification_update(
    state: OrchestratorState,
    missing_info: List[str],
    question: str = "",
    query_type: str = "general",
) -> Dict[str, Any]:
    return {
        "query_type": query_type,
        "confidence": 0.0,
        "missing_info": missing_info or ["query_scope"],
        "clarification_needed": True,
        "clarification_question": question,
        "should_escalate": False,
        **_slot_update(state),
    }


def _fallback_update(state: OrchestratorState, rule: Optional[RuleRoutingResult]) -> Dict[str, Any]:
    """LLM 不可用时，只在规则或明显歧义上做保守处理。"""

    if rule is not None:
        return _rule_update(state, rule)
    if looks_ambiguous(state.query):
        return _clarification_update(state, ["query_scope"])
    return {
        "query_type": "general",
        "confidence": 0.0,
        "missing_info": [],
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": False,
        **_slot_update(state),
    }


def _apply_llm_output(state: OrchestratorState, output: RouterLLMOutput) -> Dict[str, Any]:
    needs_clarification = output.needs_clarification
    missing_info = list(output.missing_info)

    # 低置信度且存在指代时，宁可追问一次，也不要把问题硬塞给某个子图。
    if (
        not needs_clarification
        and output.confidence < LLM_MIN_CONFIDENCE
        and looks_ambiguous(state.query)
    ):
        needs_clarification = True
        missing_info = missing_info or ["query_scope"]

    # 首轮就出现“这个/那个”且没有历史可借，模型即使很自信也应先确认对象。
    if (
        not needs_clarification
        and looks_ambiguous(state.query)
        and not state.clarification_needed
        and not build_router_history(state)
    ):
        needs_clarification = True
        missing_info = missing_info or ["query_scope"]

    if needs_clarification:
        update = _clarification_update(
            state,
            missing_info,
            question=output.clarification_question,
            query_type=output.query_type,
        )
        update["confidence"] = output.confidence
        return update

    return {
        "query_type": output.query_type,
        "confidence": output.confidence,
        "missing_info": [],
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": False,
        **_slot_update(state, output),
    }


async def _call_router_llm(state: OrchestratorState) -> RouterLLMOutput:
    prompt = build_router_prompt(
        query=state.query,
        history=build_router_history(state),
        clarification_pending=state.clarification_needed,
        clarified_info=state.clarified_info,
    )
    response = await json_llm.ainvoke(prompt)
    content = response.content
    if not isinstance(content, str):
        raise ValueError("路由模型返回内容不是文本")
    return parse_json_model(content, RouterLLMOutput)


async def router_node(state: OrchestratorState) -> dict:
    """先走规则，规则无法判断时调用 LLM。"""

    rule_result = classify_by_rules(state.query)

    # 明显单向命中且没有等待澄清时，直接使用规则，避免无意义 LLM 调用。
    if rule_result is not None and not state.clarification_needed:
        logger.info("规则路由命中", query=state.query, rule=rule_result.model_dump())
        return _rule_update(state, rule_result)

    # 等待澄清或多义词必须让模型结合历史判断。
    try:
        llm_output = await _call_router_llm(state)
    except (openai.APIError, ValueError, ValidationError, TypeError) as e:
        logger.warning("路由模型失败，使用保守兜底", query=state.query, error=str(e))
        return _fallback_update(state, rule_result)

    logger.info("LLM 路由完成", query=state.query, output=llm_output.model_dump())
    return _apply_llm_output(state, llm_output)
