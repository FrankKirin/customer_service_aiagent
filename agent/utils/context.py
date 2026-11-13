"""多轮对话上下文构建。

路由和检索不能只看当前一句，否则“那我退了行不行”会丢失上一轮
购买渠道、收货时间等关键信息。这里只读取最近的真实用户消息和已经
确认的槽位，不使用 LLM 生成摘要。
"""

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import AnyMessage, HumanMessage

CONTEXT_HISTORY_LIMIT = 3
CONTEXT_MAX_CHARS = 1200


def recent_user_questions(messages: List[AnyMessage], limit: int = 3) -> List[str]:
    """按时间顺序返回最近 limit 条非空用户消息。"""

    questions: List[str] = []
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        text = content.strip()
        if text:
            questions.append(text)
        if len(questions) >= limit:
            break
    return list(reversed(questions))


def _format_clarified_info(clarified_info: Optional[Dict[str, Any]]) -> str:
    """把已确认槽位格式化成确定性的单行文本。"""

    if not clarified_info:
        return ""
    payload = json.dumps(clarified_info, ensure_ascii=False, sort_keys=True)
    return f"已确认信息：{payload}"


def _join_with_budget(
    history: List[str],
    current_query: str,
    clarified_info_text: str,
    max_chars: int,
) -> str:
    """优先保留当前问题和已确认槽位，超长时从最旧历史开始丢弃。"""

    remaining_history = list(history)
    while True:
        parts = remaining_history + [current_query]
        if clarified_info_text:
            parts.append(clarified_info_text)
        text = "\n".join(part for part in parts if part)
        if len(text) <= max_chars or not remaining_history:
            break
        remaining_history.pop(0)

    if len(text) <= max_chars:
        return text
    # 单个当前问题本身超长时，只能截断；保留末尾更接近用户当前表达。
    return text[-max_chars:]


def build_contextual_query(
    user_query: str,
    messages: List[AnyMessage],
    clarified_info: Optional[Dict[str, Any]] = None,
    limit: int = CONTEXT_HISTORY_LIMIT,
    max_chars: int = CONTEXT_MAX_CHARS,
) -> str:
    """构建子图使用的检索/生成上下文。

    组成顺序：最近 limit 条用户消息 + 当前问题 + 已确认槽位。
    当前问题通常已经由 prepare_turn 追加到 messages 末尾，因此需要去重。
    """

    current = user_query.strip()
    history = recent_user_questions(messages, limit=limit + 1)
    if history and history[-1] == current:
        history = history[:-1]
    history = history[-limit:]

    return _join_with_budget(
        history=history,
        current_query=current,
        clarified_info_text=_format_clarified_info(clarified_info),
        max_chars=max_chars,
    )


def build_router_history(
    messages: List[AnyMessage],
    user_query: str,
    limit: int = 6,
) -> str:
    """给路由模型提供最近用户消息，不包含系统生成的回答。"""

    current = user_query.strip()
    history = recent_user_questions(messages, limit=limit + 1)
    if history and history[-1] == current:
        history = history[:-1]
    return "\n".join(history[-limit:])
