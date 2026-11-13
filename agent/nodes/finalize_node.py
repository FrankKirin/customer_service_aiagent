"""主图收尾节点：整理最终响应、引用和耗时。"""

from datetime import datetime
from typing import List

from langchain_core.messages import AIMessage

from ..state import Citation, OrchestratorState

_SOURCE_MARKER = "参考来源："


def _elapsed_ms(timestamp: datetime | str) -> float:
    if isinstance(timestamp, datetime):
        started_at = timestamp
    elif isinstance(timestamp, str):
        try:
            started_at = datetime.fromisoformat(timestamp)
        except ValueError:
            return 0.0
    else:
        return 0.0
    elapsed = (datetime.now() - started_at).total_seconds() * 1000
    return round(max(elapsed, 0.0), 2)


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
    """生成最终响应并写入历史消息。"""

    citations = _deduplicate_citations(state.source_citations)

    if state.clarification_needed:
        # 澄清轮只返回问题，不展示尚未形成结论的引用。
        citations = []
        response = state.clarification_question.strip() or state.response.strip()
    else:
        response = state.response.strip()

    should_escalate = state.should_escalate
    if not response:
        response = "抱歉，当前没有生成有效回答，请稍后重试或联系人工客服。"
        should_escalate = True

    if not state.clarification_needed:
        response = _append_source_lines(response, citations)

    return {
        "response": response,
        "source_citations": citations,
        "should_escalate": should_escalate,
        "processing_time_ms": _elapsed_ms(state.timestamp),
        "messages": [AIMessage(content=response)],
    }
