"""节点级测试：不访问网络，只验证状态更新和真实文档映射。"""

import asyncio
import json
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

import agent.nodes.router_node as router_module
from agent.nodes.clarification_node import clarification_node
from agent.nodes.finalize_node import finalize_node
from agent.nodes.prepare_node import prepare_turn_node
from agent.state import Citation, OrchestratorState, PolicyAgentState, SourceInfo


class FakeJSONLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def ainvoke(self, prompt: str) -> AIMessage:
        return AIMessage(content=json.dumps(self.payload, ensure_ascii=False))


def _policy_document() -> Document:
    return Document(
        page_content="消费者自收到产品之日起7日内，发生性能故障，消费者可以选择退货、换货或修理。",
        metadata={
            "document_id": "keg_after_sales_policy_2026",
            "source": "售后服务政策",
            "section": "服务政策/如何退、换货？/退换货规定",
            "relevance_score": 0.9,
            "chunk_id": "policy::0::0",
        },
    )


def _manual_document() -> Document:
    return Document(
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


def test_prepare_turn_resets_outputs_but_keeps_pending_clarification():
    state = OrchestratorState(
        user_query="第5天可以退吗？",
        needs_clarification=True,
        clarification_question="上一轮问题",
        final_answer="上一轮回答",
        clarified_info={"purchase_channel": "线下门店"},
        source_citations=[
            Citation(
                text="上一轮引用",
                source=SourceInfo(source_id="old", source_type="policy"),
            )
        ],
        messages=[HumanMessage(content="第5天可以退吗？")],
    )

    update = prepare_turn_node(state)

    assert update["needs_clarification"] is True
    assert update["clarification_question"] == ""
    assert update["final_answer"] == ""
    assert update["source_citations"] == []
    assert update["messages"][0].content == "第5天可以退吗？"
    assert "clarified_info" not in update


def test_router_uses_llm_output_for_non_rule_question(monkeypatch):
    async def fake_call(state):
        return router_module.RouterLLMOutput(
            query_type="general",
            confidence=0.9,
            clarified_info={},
        )

    monkeypatch.setattr(router_module, "_call_router_llm", fake_call)
    state = OrchestratorState(user_query="你好")

    update = asyncio.run(router_module.router_node(state))

    assert update["query_type"] == "general"
    assert update["needs_clarification"] is False


def test_router_turns_ambiguous_question_into_clarification(monkeypatch):
    async def fake_call(state):
        return router_module.RouterLLMOutput(
            query_type="policy",
            confidence=0.3,
            missing_info=["query_scope"],
            needs_clarification=True,
            clarified_info={},
        )

    monkeypatch.setattr(router_module, "_call_router_llm", fake_call)
    state = OrchestratorState(user_query="我这个能换吗？")

    update = asyncio.run(router_module.router_node(state))

    assert update["needs_clarification"] is True
    assert update["missing_info"] == ["query_scope"]


def test_router_keeps_llm_slots_when_clarification_is_required(monkeypatch):
    async def fake_call(state):
        return router_module.RouterLLMOutput(
            query_type="manual",
            confidence=0.9,
            needs_clarification=True,
            missing_info=["product_model"],
            clarification_question="请问您的冰箱型号是什么？",
            clarified_info={"purchase_channel": "线下门店"},
            product_model="N.30-2-31",
        )

    monkeypatch.setattr(router_module, "_call_router_llm", fake_call)
    state = OrchestratorState(user_query="这个型号怎么弄？")

    update = asyncio.run(router_module.router_node(state))

    assert update["needs_clarification"] is True
    assert update["clarified_info"]["purchase_channel"] == "线下门店"
    assert update["clarified_info"]["product_model"] == "N.30-2-31"


def test_router_forces_clarification_for_ambiguous_first_turn_even_when_confident(monkeypatch):
    async def fake_call(state):
        return router_module.RouterLLMOutput(
            query_type="general",
            confidence=0.95,
            needs_clarification=False,
            clarified_info={},
        )

    monkeypatch.setattr(router_module, "_call_router_llm", fake_call)
    state = OrchestratorState(user_query="这个怎么处理？")

    update = asyncio.run(router_module.router_node(state))

    assert update["needs_clarification"] is True
    assert update["missing_info"] == ["query_scope"]


def test_clarification_node_builds_template_question_from_missing_field():
    state = OrchestratorState(
        user_query="我这个能换吗？",
        missing_info=["query_scope"],
    )

    update = asyncio.run(clarification_node(state))

    assert update["needs_clarification"] is True
    assert "售后政策" in update["final_answer"]
    assert update["clarification_question"] == update["final_answer"]


def test_policy_analyze_builds_citation_from_retrieved_document(monkeypatch):
    import agent.subgraphs.policy_agent as policy_module

    payload = {
        "answer": "收到产品7日内发生性能故障，可以选择退货、换货或修理。",
        "applicability": "applicable",
        "conditions": ["7日内", "性能故障"],
        "violations": [],
        "used_sources": ["S1"],
        "insufficient_info": False,
        "follow_up_question": "",
    }
    monkeypatch.setattr(policy_module, "json_llm", FakeJSONLLM(payload))
    document = _policy_document()
    state = PolicyAgentState(
        user_query="收到第5天可以退货吗？",
        context_query="收到第5天可以退货吗？",
        documents=[document],
    )

    update = asyncio.run(policy_module.policy_analyze_node(state))

    assert update["policy_applicability"] == "applicable"
    assert update["policy_conditions"] == ["7日内", "性能故障"]
    assert update["source_citations"][0].source.section.endswith("退换货规定")
    assert update["should_escalate"] is False


def test_manual_generate_appends_real_safety_warning_and_steps(monkeypatch):
    import agent.subgraphs.manual_agent as manual_module

    payload = {
        "issue_summary": "说明书中给出了接通电源前的注意事项。",
        "steps": ["接通电源前阅读说明书。"],
        "used_sources": ["S1"],
        "insufficient_info": False,
        "requires_expert": False,
        "complexity_level": "low",
        "tools_needed": [],
        "follow_up_question": "",
    }
    monkeypatch.setattr(manual_module, "json_llm", FakeJSONLLM(payload))
    document = _manual_document()
    warning = "注意：接通电源前，请仔细阅读说明书，正确使用冰箱，并妥善保管。"

    state = manual_module.ManualAgentState(
        user_query="说明书里接通电源前要注意什么？",
        context_query="说明书里接通电源前要注意什么？",
        documents=[document],
        safety_warnings=[warning],
    )

    update = asyncio.run(manual_module.manual_generate_node(state))

    assert "操作步骤：" in update["final_answer"]
    assert "安全提示：" in update["final_answer"]
    assert update["steps"] == ["接通电源前阅读说明书。"]
    assert update["source_citations"][0].source.page == 2


def test_finalize_keeps_clarification_invariant_when_question_is_empty():
    state = OrchestratorState(
        user_query="这个怎么处理？",
        needs_clarification=True,
    )

    update = asyncio.run(finalize_node(state))

    assert update["final_answer"]
    assert update["should_escalate"] is False


def test_finalize_appends_source_lines_and_writes_ai_message():
    source = SourceInfo(
        source_id="售后服务政策",
        source_type="policy",
        section="服务政策/如何退、换货？/退换货规定",
        relevance_score=0.9,
    )
    state = OrchestratorState(
        user_query="可以退吗？",
        final_answer="符合7日条件可以选择退货。",
        source_citations=[Citation(text="7日内……", source=source)],
    )

    update = asyncio.run(finalize_node(state))

    assert "参考来源：" in update["final_answer"]
    assert isinstance(update["messages"][0], AIMessage)

