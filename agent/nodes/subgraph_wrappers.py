"""父图与领域子图之间的契约适配节点。

父图不共享子图 State。wrapper 负责：
1. 用父图字段构建子图输入契约 `{user_query, context_query}`；
2. 调用子图；
3. 只把输出契约字段写回父图，并落实澄清/转人工不变量。
"""

from typing import Any, Dict, Mapping

from pydantic import ValidationError

from core.logger import logger

from ..state import Citation, OrchestratorState
from ..utils.context import build_contextual_query

DEFAULT_CLARIFICATION = "请问您具体想咨询哪方面的问题？"
DEFAULT_FALLBACK_ANSWER = "抱歉，当前没有生成有效回答，请稍后重试或联系人工客服。"


def build_subgraph_input(state: OrchestratorState) -> Dict[str, str]:
    """构建两个子图共用的输入契约。"""

    user_query = state.user_query.strip()
    context_query = build_contextual_query(
        user_query=user_query,
        messages=state.messages,
        clarified_info=state.clarified_info,
    )
    return {
        "user_query": user_query,
        "context_query": context_query or user_query,
    }


def _read_output(result: Any, key: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(key)
    return getattr(result, key, None)


def _text_output(result: Any, key: str) -> str:
    value = _read_output(result, key)
    return value.strip() if isinstance(value, str) else ""


def _coerce_citations(result: Any) -> list[Citation]:
    raw_citations = _read_output(result, "source_citations")
    if not isinstance(raw_citations, list):
        return []

    citations: list[Citation] = []
    for item in raw_citations:
        if isinstance(item, Citation):
            citations.append(item)
            continue
        if not isinstance(item, dict):
            logger.warning("忽略无法识别的子图引用", item_type=type(item).__name__)
            continue
        try:
            citations.append(Citation.model_validate(item))
        except ValidationError as e:
            logger.warning("忽略结构不合法的子图引用", error=str(e))
    return citations


def translate_subgraph_output(result: Any) -> Dict[str, Any]:
    """把子图输出契约转换成父图更新，保证输出不变量。"""

    final_answer = _text_output(result, "final_answer")
    clarification_question = _text_output(result, "clarification_question")
    needs_clarification = bool(_read_output(result, "needs_clarification"))
    should_escalate = bool(_read_output(result, "should_escalate"))
    source_citations = _coerce_citations(result)

    if needs_clarification:
        # 澄清优先于转人工：澄清轮不展示引用，也不宣称已经转人工。
        should_escalate = False
        source_citations = []
        clarification_question = (
            clarification_question or final_answer or DEFAULT_CLARIFICATION
        )
        final_answer = final_answer or clarification_question
    elif not final_answer:
        final_answer = DEFAULT_FALLBACK_ANSWER
        should_escalate = True

    return {
        "final_answer": final_answer,
        "source_citations": source_citations,
        "should_escalate": should_escalate,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
    }


def _build_wrapper_node(graph: Any, node_name: str):
    async def wrapper_node(state: OrchestratorState) -> dict:
        result = await graph.ainvoke(build_subgraph_input(state))
        update = translate_subgraph_output(result)
        logger.info(
            "子图执行完成",
            node=node_name,
            query_type=state.query_type,
            needs_clarification=update["needs_clarification"],
            should_escalate=update["should_escalate"],
            citation_count=len(update["source_citations"]),
        )
        return update

    return wrapper_node


def build_policy_wrapper_node(policy_graph: Any):
    """创建政策子图 wrapper 节点。"""

    return _build_wrapper_node(policy_graph, "policy_agent")


def build_manual_wrapper_node(manual_graph: Any):
    """创建说明书子图 wrapper 节点。"""

    return _build_wrapper_node(manual_graph, "manual_agent")
