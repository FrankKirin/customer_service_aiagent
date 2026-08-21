# 负责搭建图
from langgraph.graph import StateGraph, START, END

"""
START
 ↓
intent
 ↓
conditional_edges
 ├── faq
 ├── quote
 └── general
"""