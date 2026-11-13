"""说明书问答子图。

流程：检索真实说明书 -> 结构化生成步骤 -> 从原文抽取安全提示。
不编造故障原因、零件名称、维修参数或安全提示。
"""

from typing import List, Literal, Sequence

import chromadb
import openai
from langchain_core.documents import Document
from pydantic import BaseModel, Field, ValidationError

from core.logger import logger
from langgraph.graph import END, START, StateGraph
from llm.base_llm import json_llm

from ..prompts.manual_prompt import MANUAL_PROMPT_TEMPLATE
from ..state import ManualAgentState
from ..tools.manual_retriever import ManualRetrieverTool
from ..utils.context import build_contextual_query
from ..utils.documents import (
    build_citations,
    build_source_info,
    calculate_retrieval_confidence,
    documents_to_prompt_context,
    normalize_documents,
)
from ..utils.json_parser import parse_json_model
from ..utils.safety import extract_safety_warnings

MANUAL_TOP_K = 8


class ManualAnswer(BaseModel):
    """说明书问答模型的结构化输出。"""

    issue_summary: str  
    steps: List[str] = Field(default_factory=list)
    used_sources: List[str] = Field(default_factory=list)
    insufficient_info: bool = False
    requires_expert: bool = False
    complexity_level: Literal["low", "medium", "high"] = "low"
    tools_needed: List[str] = Field(default_factory=list)
    follow_up_question: str = ""


def _no_manual_documents_response() -> dict:
    return {
        "query_type": "manual",
        "response": "抱歉，当前说明书知识库没有检索到足够依据，暂时无法确认具体操作步骤。请补充产品型号、故障现象或页码，或联系人工客服进一步核实。",
        "retrieved_docs": [],
        "source_info": [],
        "source_citations": [],
        "retrieval_confidence": 0.0,
        "step_by_step_guide": False,
        "safety_warnings": [],
        "tools_needed": [],
        "requires_expert": False,
        "complexity_level": "low",
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": True,
    }


async def manual_retrieve_node(state: ManualAgentState) -> dict:
    """从真实说明书知识库检索。"""

    query = build_contextual_query(state)
    try:
        documents = await ManualRetrieverTool.ainvoke({"query": query, "top_k": MANUAL_TOP_K})
    except (chromadb.errors.ChromaError, openai.APIError, ValueError) as e:
        logger.error("说明书检索失败", query=query, error=str(e))
        response = _no_manual_documents_response()
        response["response"] = "抱歉，说明书检索服务暂时不可用，请稍后重试或联系人工客服。"
        return response

    documents = normalize_documents(documents)
    if not documents:
        return _no_manual_documents_response()

    source_info = build_source_info(documents, "manual")
    safety_warnings = extract_safety_warnings(documents)
    return {
        "query_type": "manual",
        "retrieved_docs": documents,
        "source_info": source_info,
        "source_citations": [],
        "retrieval_confidence": calculate_retrieval_confidence(documents),
        "step_by_step_guide": False,
        "safety_warnings": safety_warnings,
        "tools_needed": [],
        "requires_expert": False,
        "complexity_level": "low",
        "clarification_needed": False,
        "clarification_question": "",
        "should_escalate": False,
    }


def _filter_tools_by_documents(
    tools: Sequence[str], documents: List[Document]
) -> List[str]:
    """只保留说明书原文中明确出现的工具。"""

    document_text = "\n".join(document.page_content for document in documents)
    filtered: List[str] = []
    for tool in tools:
        candidate = tool.strip()
        if not candidate or candidate in filtered:
            continue
        if candidate in document_text:
            filtered.append(candidate)
    return filtered


def _format_manual_response(
    summary: str,
    steps: List[str],
    tools_needed: List[str],
    safety_warnings: List[str],
) -> str:
    """把结构化结果组织成用户可读的回答。"""

    parts: List[str] = []
    if summary:
        parts.append(summary)

    if steps:
        step_lines = [f"{index}. {step}" for index, step in enumerate(steps, start=1)]
        parts.append("操作步骤：\n" + "\n".join(step_lines))

    if tools_needed:
        parts.append("所需工具：" + "、".join(tools_needed))

    if safety_warnings:
        warning_lines = [f"- {warning}" for warning in safety_warnings]
        parts.append("安全提示：\n" + "\n".join(warning_lines))

    return "\n\n".join(parts)


async def manual_generate_node(state: ManualAgentState) -> dict:
    """依据真实说明书原文生成步骤，并从原文抽取安全提示。"""

    documents = state.retrieved_docs
    if not documents:
        return {}

    prompt = MANUAL_PROMPT_TEMPLATE.format(
        documents=documents_to_prompt_context(documents),
        question=build_contextual_query(state),
    )

    try:
        response = await json_llm.ainvoke(prompt)
        content = response.content
        if not isinstance(content, str):
            raise ValueError("说明书分析模型返回内容不是文本")
        answer = parse_json_model(content, ManualAnswer)
    except (openai.APIError, ValueError, ValidationError, TypeError) as e:
        logger.error("说明书结构化回答失败", query=state.query, error=str(e))
        return {
            "response": "已检索到说明书原文，但回答生成失败。请稍后重试，或联系人工客服核实。",
            "step_by_step_guide": False,
            "source_citations": [],
            "should_escalate": True,
        }

    documents = state.retrieved_docs
    citations = build_citations(
        answer.used_sources,
        documents,
        "manual",
        fallback_count=0 if answer.insufficient_info else 3,
    )
    tools_needed = _filter_tools_by_documents(answer.tools_needed, documents)
    steps = [step.strip() for step in answer.steps if step.strip()]
    safety_warnings = state.safety_warnings

    response_text = _format_manual_response(
        summary=answer.issue_summary.strip(),
        steps=steps,
        tools_needed=tools_needed,
        safety_warnings=safety_warnings,
    )
    if not response_text:
        response_text = (
            "当前说明书原文不足以形成明确步骤，请补充产品型号、故障现象或页码，"
            "或联系人工客服进一步核实。"
        )

    clarification_needed = bool(answer.follow_up_question.strip())
    should_escalate = (
        answer.requires_expert
        or answer.complexity_level == "high"
        or (answer.insufficient_info and not clarification_needed)
        or not citations
    )

    logger.info(
        "说明书问答完成",
        query=state.query,
        step_count=len(steps),
        tools_needed=tools_needed,
        safety_warning_count=len(safety_warnings),
        citation_count=len(citations),
    )
    return {
        "response": response_text,
        "source_citations": citations,
        "step_by_step_guide": bool(steps),
        "safety_warnings": safety_warnings,
        "tools_needed": tools_needed,
        "requires_expert": answer.requires_expert,
        "complexity_level": answer.complexity_level,
        "clarification_needed": clarification_needed,
        "clarification_question": answer.follow_up_question.strip(),
        "should_escalate": should_escalate,
    }


def build_manual_agent():
    """编译说明书问答子图。"""

    graph = StateGraph(ManualAgentState)
    graph.add_node("manual_retrieve", manual_retrieve_node)
    graph.add_node("manual_generate", manual_generate_node)

    graph.add_edge(START, "manual_retrieve")
    graph.add_edge("manual_retrieve", "manual_generate")
    graph.add_edge("manual_generate", END)
    return graph.compile()

