# 负责搭建图
from langgraph.graph import StateGraph, START, END
from llm.base_llm import get_llm
from .state import AgentState   # 同目录下使用相对导入
from .nodes.faq_node import faq_node
from .nodes.general_node import general_node
from .nodes.quote_node import quote_node
from .nodes.intent_node import intent_node
from .router import route_by_intent
from langchain_core.messages import HumanMessage

llm = get_llm()

def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("intent_node", intent_node)
    graph.add_node("faq_node", faq_node)
    graph.add_node("quote_node", quote_node)
    graph.add_node("general_node", general_node)

    graph.add_edge(START, "intent_node")
    graph.add_conditional_edges(
        "intent_node",
        route_by_intent,
        ["faq_node", "quote_node", "general_node"]
    )
    graph.add_edge("faq_node", END)
    graph.add_edge("quote_node", END)
    graph.add_edge("general_node", END)

    return graph.compile()

async def main(query: str):
    workflow = build_agent_graph()
    result = await workflow.ainvoke({
        "messages": [HumanMessage(content=query)],
        "original_query": query,
        "intent": "",
        "final_answer": "" 
    })
    print(result["final_answer"])

    
if __name__ == "__main__":
    import asyncio
    query = "长滩到上海的40高柜多少钱?"
    asyncio.run(main(query))

# class AgentState(TypedDict):
#     messages: Annotated[list[AnyMessage], operator.add]
#     original_query: str
#     intent: str
#     final_answer: str
