from ..state import AgentState
from llm.base_llm import get_llm

async def faq_node(state: AgentState):
    print("走了FAQ这个node")
    llm = get_llm("json")
    result = await llm.ainvoke(state["original_query"])

    return {
        "intent": "faq",
        "final_answer": result.content
    }