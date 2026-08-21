# 负责搭建图
from langgraph.graph import StateGraph, START, END
from llm.base_llm import get_llm
from state import AgentState
from nodes.faq_node import FAQNode
from nodes.general_node import GeneralNode
from nodes.quote_node import QuoteNode
from nodes.intent_node import IntentNode
from router import route_by_intent
from langchain_core.messages import HumanMessage

llm = get_llm()

def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("intent_node", IntentNode)
    graph.add_node("faq_node", FAQNode)
    graph.add_node("quote_node", QuoteNode)
    graph.add_node("general_node", GeneralNode)

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
    
if __name__ == "__main__":
    workflow = build_agent_graph()
    query = "你是什么模型，具体的名字是什么?"
    result = workflow({
        "messages": HumanMessage(content=query),
        "original_query": query,
        "intent": "",
        "final_answer": ""
    })
    print(result["final_answer"])

# class AgentState(TypedDict):
#     messages: Annotated[list[AnyMessage], operator.add]
#     original_query: str
#     intent: str
#     final_answer: str
