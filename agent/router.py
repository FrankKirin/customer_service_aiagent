
def route_by_intent(state):
    if state["intent"] == "faq":
        return "faq_node"
    elif state["intent"] == "quote":
        return "quote_node"
    else:
        return "general_node"