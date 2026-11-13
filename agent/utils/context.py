"""多轮对话上下文构建。

路由和检索不能只看当前一句，否则“那我退了行不行”会丢失上一轮
购买渠道、收货时间等关键信息。这里只读取最近的真实用户消息，不生成
任何额外内容。
"""

from typing import List

from langchain_core.messages import AnyMessage, HumanMessage

from ..state import SharedState


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


def build_contextual_query(state: SharedState, limit: int = 3, max_chars: int = 1200) -> str:
    """把当前问题和最近用户消息拼成检索/路由查询。

    当前问题可能已经被 prepare_turn 追加到 messages 末尾，因此需要避免重复。
    """

    current = state.query.strip()
    history = recent_user_questions(state.messages, limit=limit + 1)
    if history and history[-1] == current:
        history = history[:-1]
    history = history[-limit:]

    parts = history + ([current] if current else [])
    if not parts:
        return ""
    text = "\n".join(parts)
    if len(text) <= max_chars:
        return text
    # 保留离当前问题最近的上下文，避免超长历史挤占提示词预算。
    return text[-max_chars:]


def build_router_history(state: SharedState, limit: int = 6) -> str:
    """给路由模型提供最近用户消息，不包含系统生成的回答。"""

    history = recent_user_questions(state.messages, limit=limit)
    if history and history[-1] == state.query.strip():
        history = history[:-1]
    return "\n".join(history[-limit:])
