import operator
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph
from langchain.messages import AnyMessage


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    original_query: str
    intent: str
    final_answer: str