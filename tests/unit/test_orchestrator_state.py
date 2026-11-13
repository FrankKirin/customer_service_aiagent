"""主图状态结构测试。"""

from agent.state import (
    Citation,
    ManualAgentState,
    OrchestratorState,
    PolicyAgentState,
    SharedState,
    SourceInfo,
)


def test_orchestrator_declares_all_subgraph_fields():
    """子图返回的字段必须在父图中存在，否则会被 LangGraph 丢弃。"""

    shared_fields = set(SharedState.model_fields)
    policy_fields = set(PolicyAgentState.model_fields) - shared_fields
    manual_fields = set(ManualAgentState.model_fields) - shared_fields
    expected = shared_fields | policy_fields | manual_fields

    assert set(OrchestratorState.model_fields) == expected
    assert {"product_line", "policy_applicability", "policy_violations"} <= policy_fields
    assert {"product_model", "safety_warnings", "requires_expert"} <= manual_fields


def test_policy_and_manual_fields_have_expected_types():
    state = OrchestratorState(query="测试问题", session_id="session-1")

    assert state.policy_conditions == {}
    assert state.policy_violations == []
    assert state.safety_warnings == []
    assert state.complexity_level == "low"


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
