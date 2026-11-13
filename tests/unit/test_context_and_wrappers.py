"""上下文构建与父子图契约适配测试。"""

from langchain_core.messages import AIMessage, HumanMessage

from agent.nodes.subgraph_wrappers import (
    build_subgraph_input,
    translate_subgraph_output,
)
from agent.state import (
    Citation,
    ManualAgentState,
    OrchestratorState,
    SourceInfo,
)
from agent.subgraphs.manual_agent import _resolve_issue_type
from agent.utils.context import build_contextual_query


def test_context_query_uses_recent_user_messages_and_clarified_info():
    messages = [
        HumanMessage(content="第一轮问题"),
        AIMessage(content="第一轮回答"),
        HumanMessage(content="第二轮问题"),
        HumanMessage(content="第三轮问题"),
        HumanMessage(content="第四轮问题"),
    ]

    context = build_contextual_query(
        user_query="当前问题",
        messages=messages,
        clarified_info={"issue_type": "troubleshooting"},
        max_chars=1200,
    )

    assert "第一轮问题" not in context
    assert "第二轮问题" in context
    assert "第四轮问题" in context
    assert context.endswith('已确认信息：{"issue_type": "troubleshooting"}')
    assert "第一轮回答" not in context


def test_context_query_respects_max_length():
    context = build_contextual_query(
        user_query="很长的当前问题" * 20,
        messages=[HumanMessage(content="历史问题" * 20)],
        clarified_info={"product_model": "N.30-2-31"},
        max_chars=40,
    )

    assert len(context) <= 40


def test_wrapper_input_contains_only_contract_fields_and_context():
    state = OrchestratorState(
        user_query="冰箱不制冷怎么处理？",
        clarified_info={"issue_type": "troubleshooting"},
        messages=[HumanMessage(content="冰箱不制冷怎么处理？")],
    )

    payload = build_subgraph_input(state)

    assert set(payload) == {"user_query", "context_query"}
    assert payload["user_query"] == "冰箱不制冷怎么处理？"
    assert "issue_type" in payload["context_query"]


def test_wrapper_clears_citations_and_escalation_during_clarification():
    update = translate_subgraph_output(
        {
            "final_answer": "请补充产品型号。",
            "source_citations": [
                Citation(
                    text="来源片段",
                    source=SourceInfo(source_id="manual", source_type="manual"),
                )
            ],
            "should_escalate": True,
            "needs_clarification": True,
            "clarification_question": "请问您的产品型号是什么？",
        }
    )

    assert update["needs_clarification"] is True
    assert update["should_escalate"] is False
    assert update["source_citations"] == []
    assert update["clarification_question"] == "请问您的产品型号是什么？"


def test_wrapper_adds_fallback_answer_when_subgraph_returns_empty_output():
    update = translate_subgraph_output(
        {
            "final_answer": "",
            "source_citations": [],
            "should_escalate": False,
            "needs_clarification": False,
            "clarification_question": "",
        }
    )

    assert update["final_answer"]
    assert update["should_escalate"] is True


def test_manual_issue_type_is_extracted_from_explicit_query():
    state = ManualAgentState(
        user_query="冰箱不制冷，出现故障码 E1，怎么排查？",
        context_query="冰箱不制冷，出现故障码 E1，怎么排查？",
    )

    assert _resolve_issue_type(state) == "troubleshooting"
