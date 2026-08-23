from ..state import AgentState
from llm.base_llm import json_llm
from ..prompts.intent_prompt import INTENT_PROMPT
from core.logger import logger
from langchain.messages import HumanMessage, SystemMessage
import json
from typing import Literal
from pydantic import BaseModel, ValidationError, Field

class IntentResult(BaseModel):
    intent: Literal["faq", "quote", "general"]
    confidence: float = Field(ge=0, le=1)

async def intent_node(state: AgentState) -> dict:
    query = state["original_query"]

    try:
        result = await json_llm.ainvoke([
            SystemMessage(content=INTENT_PROMPT),
            HumanMessage(content=query)
        ])

        if not result.content:
            raise ValueError("Intent LLM retuned empty content")

        data = json.loads(result.content)

        print(f"intent node里看一下调用llm传回的data内容:\n")
        print(data)
        intent_result = IntentResult.model_validate(data)

        logger.info(
            "Intent classification succeeded",
            intent=intent_result.intent,
        )

        return {
            "intent": intent_result.intent,
        }

    except json.JSONDecodeError:  # 模型返回不是合法的JSON
        logger.exception("Intent LLM returned invalid JSON")
        raise   # 记录了异常，往上抛异常，本层进捕获+日志记录
    except ValidationError:  # 业务字段不合法
        logger.exception("Invalid intent schema")
        raise
    except Exception as e:
        logger.exception("Intent classification failed")
        raise
