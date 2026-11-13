"""主图端到端测试：使用真实图结构，外部检索和 LLM 用测试替身。"""

import asyncio
import json
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import agent.main_graph as main_graph
import agent.subgraphs.policy_agent as policy_agent


class FakeJSONLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def ainvoke(self, prompt: str) -> AIMessage:
        return AIMessage(content=json.dumps(self.payload, ensure_ascii=False))


class FakePolicyRetriever:
    async def ainvoke(self, payload: dict[str, Any]) -> list[Document]:
        return [
            Document(
                page_content="消费者自收到产品之日起7日内，发生性能故障，消费者可以选择退货、换货或修理。",
                metadata={
                    "document_id": "keg_after_sales_policy_2026",
                    "source": "售后服务政策",
                    "section": "服务政策/如何退、换货？/退换货规定",
                    "relevance_score": 0.9,
                    "chunk_id": "keg_after_sales_policy_2026::0::2",
                },
            )
        ]


def test_policy_turn_runs_through_main_graph_and_returns_citation(monkeypatch):
    payload = {
        "answer": "收到产品7日内发生性能故障，可以选择退货、换货或修理。",
        "applicability": "applicable",
        "conditions": ["7日内", "性能故障"],
        "violations": [],
        "used_sources": ["S1"],
        "insufficient_info": False,
        "follow_up_question": "",
    }
    monkeypatch.setattr(policy_agent, "PolicyRetrieverTool", FakePolicyRetriever())
    monkeypatch.setattr(policy_agent, "json_llm", FakeJSONLLM(payload))
    monkeypatch.setattr(main_graph, "get_checkpointer", lambda: InMemorySaver())
    main_graph.get_main_graph.cache_clear()

    try:
        result = asyncio.run(
            main_graph.process_query(
                query="我在门店买的冰箱，收到货第5天发现制冷出了问题，可以要求退货吗？",
                session_id="unit-policy-session",
            )
        )
    finally:
        main_graph.get_main_graph.cache_clear()

    assert result.query_type == "policy"
    assert "7日内" in result.response
    assert "参考来源：" in result.response
    assert result.source_citations[0].source.source_id == "keg_after_sales_policy_2026"
    assert result.processing_time_ms >= 0

class SequenceJSONLLM:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.index = 0

    async def ainvoke(self, prompt: str) -> AIMessage:
        payload = self.payloads[self.index]
        self.index += 1
        return AIMessage(content=json.dumps(payload, ensure_ascii=False))


def test_clarification_then_policy_answer_reuses_same_thread(monkeypatch):
    import agent.nodes.router_node as router_module

    router_payloads = [
        {
            "query_type": "general",
            "confidence": 0.2,
            "missing_info": ["query_scope"],
            "needs_clarification": True,
            "clarification_question": "您是想咨询售后政策，还是产品使用问题？",
            "clarified_info": {},
        },
        {
            "query_type": "policy",
            "confidence": 0.9,
            "missing_info": [],
            "needs_clarification": False,
            "clarification_question": "",
            "clarified_info": {},
        },
    ]
    policy_payload = {
        "answer": "收到产品7日内发生性能故障，可以选择退货、换货或修理。",
        "applicability": "applicable",
        "conditions": ["7日内", "性能故障"],
        "violations": [],
        "used_sources": ["S1"],
        "insufficient_info": False,
        "follow_up_question": "",
    }
    monkeypatch.setattr(router_module, "json_llm", SequenceJSONLLM(router_payloads))
    monkeypatch.setattr(policy_agent, "PolicyRetrieverTool", FakePolicyRetriever())
    monkeypatch.setattr(policy_agent, "json_llm", FakeJSONLLM(policy_payload))
    monkeypatch.setattr(main_graph, "get_checkpointer", lambda: InMemorySaver())
    main_graph.get_main_graph.cache_clear()
    session_id = "unit-multi-turn-session"

    try:
        first = asyncio.run(
            main_graph.process_query(query="我这个能换吗？", session_id=session_id)
        )
        second = asyncio.run(
            main_graph.process_query(
                query="我在门店买的冰箱，收到货第5天发现制冷出了问题，可以要求退货吗？",
                session_id=session_id,
            )
        )
    finally:
        main_graph.get_main_graph.cache_clear()

    assert first.response == "您是想咨询售后政策，还是产品使用问题？"
    assert second.query_type == "policy"
    assert "7日内" in second.response


class FakeManualRetriever:
    async def ainvoke(self, payload: dict[str, Any]) -> list[Document]:
        return [
            Document(
                page_content="注意：\n接通电源前，请仔细阅读说明书，正确使用冰箱，并妥善保管。",
                metadata={
                    "document_id": "家用冰箱通用说明书_N.30-2-31",
                    "source": "家用冰箱通用说明书_N.30-2-31",
                    "file_name": "家用冰箱通用说明书_N.30-2-31.pdf",
                    "page": 2,
                    "relevance_score": 0.8,
                    "chunk_id": "manual::2::0",
                },
            )
        ]


def test_manual_turn_runs_through_main_graph_and_adds_safety_warning(monkeypatch):
    import agent.subgraphs.manual_agent as manual_agent

    payload = {
        "issue_summary": "说明书中说明了通电前的准备要求。",
        "steps": ["接通电源前阅读说明书。"],
        "used_sources": ["S1"],
        "insufficient_info": False,
        "requires_expert": False,
        "complexity_level": "low",
        "tools_needed": [],
        "follow_up_question": "",
    }
    monkeypatch.setattr(manual_agent, "ManualRetrieverTool", FakeManualRetriever())
    monkeypatch.setattr(manual_agent, "json_llm", FakeJSONLLM(payload))
    monkeypatch.setattr(main_graph, "get_checkpointer", lambda: InMemorySaver())
    main_graph.get_main_graph.cache_clear()

    try:
        result = asyncio.run(
            main_graph.process_query(
                query="冰箱显示屏出现 E1 故障码，是什么意思？该怎么处理？",
                session_id="unit-manual-session",
            )
        )
    finally:
        main_graph.get_main_graph.cache_clear()

    assert result.query_type == "manual"
    assert "操作步骤：" in result.response
    assert "安全提示：" in result.response
    assert result.source_citations[0].source.page == 2

