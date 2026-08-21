from state import AgentState
from llm.base_llm import get_llm

async def FAQNode(state: AgentState):
    print("走了FAQ这个node")
    llm = get_llm()
    result = await llm.ainvoke(state["original_query"])
    state["final_answer"] = result["content"]

    return state['intent'] = "faq"