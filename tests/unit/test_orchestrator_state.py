"""主图与子图状态结构测试。"""

from agent.state import (
    Citation,
    ManualAgentState,
    OrchestratorState,
    PolicyAgentState,
    SourceInfo,
)


def test_orchestrator_state_is_thin_and_does_not_inherit_subgraph_fields():
    """父图只保留跨轮/对外/决策字段，不复制子图领域字段。"""

    assert set(OrchestratorState.model_fields) == {
        "user_query",
        "final_answer",
        "query_type",
        "needs_clarification",
        "clarification_question",
        "clarified_info",
        "missing_info",
        "messages",
        "source_citations",
        "should_escalate",
    }

    assert "documents" not in OrchestratorState.model_fields
    assert "safety_warnings" not in OrchestratorState.model_fields
    assert "policy_conditions" not in OrchestratorState.model_fields


def test_subgraph_states_declare_input_and_output_contract_fields():
    expected_output_fields = {
        "final_answer",
        "source_citations",
        "should_escalate",
        "needs_clarification",
        "clarification_question",
    }

    for state_type in (PolicyAgentState, ManualAgentState):
        fields = set(state_type.model_fields)
        assert {"user_query", "context_query"} <= fields
        assert expected_output_fields <= fields

    assert "steps" in ManualAgentState.model_fields


def test_policy_and_manual_fields_have_expected_types():
    policy_state = PolicyAgentState(
        user_query="收到第5天可以退货吗？",
        context_query="收到第5天可以退货吗？",
    )
    manual_state = ManualAgentState(
        user_query="冰箱不制冷怎么处理？",
        context_query="冰箱不制冷怎么处理？",
    )
    parent_state = OrchestratorState(user_query="测试问题")

    assert policy_state.policy_conditions == []
    assert policy_state.policy_violations == []
    assert policy_state.policy_applicability == "unknown"
    assert manual_state.steps == []
    assert manual_state.safety_warnings == []
    assert manual_state.complexity_level == "low"
    assert parent_state.final_answer == ""
    assert parent_state.clarified_info == {}


def test_citation_keeps_real_source_structure():
    source = SourceInfo(
        source_id="售后服务政策",
        source_type="policy",
        section="服务政策/退换货规定",
        relevance_score=0.8,
    )
    citation = Citation(text="消费者自收到产品之日起7日内……", source=source)

    assert citation.source.source_id == "售后服务政策"
    assert "政策条款" in str(citation.source)
    assert "7日" in citation.text
