"""会话级 LangGraph 配置。

当前项目只使用 InMemorySaver：进程内有效，重启会丢失，且只适合单 worker。
"""

from functools import lru_cache
from typing import Any, Dict

from langgraph.checkpoint.memory import InMemorySaver


@lru_cache(maxsize=1)
def get_checkpointer() -> InMemorySaver:
    """编译图时复用的内存 checkpoint。"""

    return InMemorySaver()


def build_thread_config(session_id: str) -> Dict[str, Any]:
    """用 session_id 作为 thread_id，实现同一会话的多轮记忆。"""

    return {"configurable": {"thread_id": session_id}}
