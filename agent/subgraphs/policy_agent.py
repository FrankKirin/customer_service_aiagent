"""政策问答子图。

流程：检索真实政策 -> 结构化分析 -> 生成真实引用。
检索不到或模型输出不合法时明确兜底，不编造政策内容。
"""

from typing import List, Literal

import chromadb
import openai
from pydantic import BaseModel, Field, ValidationError

from core.logger import logger
from langgraph.graph import END, START, StateGraph
from llm.base_llm import json_llm

from ..prompts.policy_prompt import POLICY_PROMPT_TEMPLATE
from ..state import PolicyAgentState
from ..tools.policy_retriever import PolicyRetrieverTool
from ..utils.context import build_contextual_query
from ..utils.documents import (
    build_citations,
    build_source_info,
    calculate_retrieval_confidence,
    documents_to_prompt_context,
    normalize_documents,
)
from ..utils.json_parser import parse_json_model

POLICY_TOP_K = 5


class PolicyAnswer(BaseModel):
    """政策问答模型的结构化输出。"""

    answer: str
    applicability: Literal["applicable", "not_applicable", "conditional", "unknown"] = "unknown"
    conditions: List[str] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list)
    used_sources: List[str] = Field(default_factory=list)
    insufficient_info: bool = False
    follow_up_question: str = ""


def _no_policy_documents_response() -> dict:
    return {
        "query_type": "policy",
        "response": "抱歉，当前政策知识库没有检索到足够依据，暂时无法确认这项售后政策。请补充购买渠道、收货时间或故障情况，或联系人工客服进一步核实。",
        "retrieved_docs": [],
        "source_info": [],
        "source_citations": [],
        "retrieval_confidence": 0.0,
        "policy_applicability": "unknown",
        "policy_conditions": {},
        "policy_summary": "",
        "policy_violations": [],
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": True,
    }


async def policy_retrieve_node(state: PolicyAgentState) -> dict:
    """从真实政策知识库检索，不在任务3使用不可靠的元数据过滤。"""

    query = build_contextual_query(state)
    try:
        documents = await PolicyRetrieverTool.ainvoke({"query": query, "top_k": POLICY_TOP_K})
    except (chromadb.errors.ChromaError, openai.APIError, ValueError) as e:
        logger.error("政策检索失败", query=query, error=str(e))
        response = _no_policy_documents_response()
        response["response"] = "抱歉，政策检索服务暂时不可用，请稍后重试或联系人工客服。"
        return response

    documents = normalize_documents(documents)
    if not documents:
        return _no_policy_documents_response()

    source_info = build_source_info(documents, "policy")
    return {
        "query_type": "policy",
        "retrieved_docs": documents,
        "source_info": source_info,
        "source_citations": [],
        "retrieval_confidence": calculate_retrieval_confidence(documents),
        "policy_applicability": "unknown",
        "policy_conditions": {},
        "policy_summary": "",
        "policy_violations": [],
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": False,
    }


async def policy_analyze_node(state: PolicyAgentState) -> dict:
    """依据真实政策原文生成结构化回答和引用。"""

    documents = state.retrieved_docs
    if not documents:
        return {}

    prompt = POLICY_PROMPT_TEMPLATE.format(
        documents=documents_to_prompt_context(documents),
        question=build_contextual_query(state),
    )

    try:
        response = await json_llm.ainvoke(prompt)
        content = response.content
        if not isinstance(content, str):
            raise ValueError("政策分析模型返回内容不是文本")
        answer = parse_json_model(content, PolicyAnswer)
    except (openai.APIError, ValueError, ValidationError, TypeError) as e:
        logger.error("政策结构化回答失败", query=state.query, error=str(e))
        return {
            "response": "已检索到政策原文，但回答生成失败。请稍后重试，或联系人工客服核实。",
            "policy_applicability": "unknown",
            "source_citations": [],
            "should_escalate": True,
        }

    citations = build_citations(
        answer.used_sources,
        documents,
        "policy",
        fallback_count=0 if answer.insufficient_info else 3,
    )
    response_text = answer.answer.strip()
    if not response_text:
        response_text = (
            "当前政策原文不足以形成明确回答，请补充购买渠道、收货时间或故障情况，"
            "或联系人工客服进一步核实。"
        )

    clarification_needed = bool(answer.follow_up_question.strip())
    should_escalate = (answer.insufficient_info and not clarification_needed) or not citations

    logger.info(
        "政策问答完成",
        query=state.query,
        applicability=answer.applicability,
        used_sources=answer.used_sources,
        citation_count=len(citations),
    )
    return {
        "response": response_text,
        "source_citations": citations,
        "policy_applicability": answer.applicability,
        "policy_conditions": {
            "conditions": answer.conditions,
            "violations": answer.violations,
        },
        "policy_summary": response_text,
        "policy_violations": answer.violations,
        "clarification_needed": clarification_needed,
        "clarification_question": answer.follow_up_question.strip(),
        "should_escalate": should_escalate,
    }


def build_policy_agent():
    """编译政策问答子图。"""

    graph = StateGraph(PolicyAgentState)
    graph.add_node("policy_retrieve", policy_retrieve_node)
    graph.add_node("policy_analyze", policy_analyze_node)

    graph.add_edge(START, "policy_retrieve")
    graph.add_edge("policy_retrieve", "policy_analyze")
    graph.add_edge("policy_analyze", END)
    return graph.compile()

