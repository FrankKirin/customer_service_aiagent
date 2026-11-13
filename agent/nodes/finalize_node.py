"""主图收尾节点：整理最终回答、引用和历史消息。"""

from typing import List

from langchain_core.messages import AIMessage

from ..state import Citation, OrchestratorState

_SOURCE_MARKER = "参考来源："
_EMPTY_ANSWER_FALLBACK = "抱歉，当前没有生成有效回答，请稍后重试或联系人工客服。"
_EMPTY_CLARIFICATION_FALLBACK = "请问您具体想咨询哪方面的问题？"


def _deduplicate_citations(citations: List[Citation]) -> List[Citation]:
    unique: List[Citation] = []
    seen: set[tuple[str, str | None, int | None]] = set()
    for citation in citations:
        key = (
            citation.source.source_id,
            citation.source.section,
            citation.source.page,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(citation)
    return unique


def _append_source_lines(response: str, citations: List[Citation]) -> str:
    if not citations or _SOURCE_MARKER in response:
        return response

    source_lines: List[str] = []
    seen: set[str] = set()
    for citation in citations:
        text = str(citation.source)
        if text in seen:
            continue
        seen.add(text)
        source_lines.append(f"- {text}")

    if not source_lines:
        return response
    return f"{response}\n\n{_SOURCE_MARKER}\n" + "\n".join(source_lines)


async def finalize_node(state: OrchestratorState) -> dict:
    """生成最终回答并写入历史消息。"""

    citations = _deduplicate_citations(state.source_citations)

    if state.needs_clarification:
        # 澄清轮只返回问题，不展示尚未形成结论的引用，也不宣称已经转人工。
        citations = []
        response = (
            state.clarification_question.strip()
            or state.final_answer.strip()
            or _EMPTY_CLARIFICATION_FALLBACK
        )
        should_escalate = False
    else:
        response = state.final_answer.strip()
        should_escalate = state.should_escalate
        if not response:
            response = _EMPTY_ANSWER_FALLBACK
            should_escalate = True
        response = _append_source_lines(response, citations)

    return {
        "final_answer": response,
        "source_citations": citations,
        "should_escalate": should_escalate,
        "messages": [AIMessage(content=response)],
    }
