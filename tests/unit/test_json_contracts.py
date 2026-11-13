"""LLM 结构化输出契约测试。"""

import pytest
from pydantic import ValidationError

from agent.nodes.router_node import RouterLLMOutput
from agent.subgraphs.manual_agent import ManualAnswer
from agent.subgraphs.policy_agent import PolicyAnswer
from agent.utils.json_parser import extract_json_object, parse_json_model


def test_extract_json_object_handles_markdown_fence():
    content = """```json
    {"answer": "可以", "used_sources": ["S1"]}
    ```
    """
    assert extract_json_object(content) == {"answer": "可以", "used_sources": ["S1"]}


def test_extract_json_object_rejects_text_without_object():
    with pytest.raises(ValueError):
        extract_json_object("模型只返回了一段说明，没有 JSON")


def test_policy_contract_accepts_valid_payload():
    payload = """```json
    {
      "answer": "收到产品7日内发生性能故障，可以选择退货、换货或修理。",
      "applicability": "applicable",
      "conditions": ["7日内", "性能故障"],
      "violations": [],
      "used_sources": ["S1"],
      "insufficient_info": false,
      "follow_up_question": ""
    }
    ```"""

    answer = parse_json_model(payload, PolicyAnswer)
    assert answer.applicability == "applicable"
    assert answer.used_sources == ["S1"]


def test_policy_contract_rejects_unknown_applicability():
    payload = {
        "answer": "回答",
        "applicability": "maybe",
        "used_sources": ["S1"],
    }
    with pytest.raises(ValidationError):
        PolicyAnswer.model_validate(payload)


def test_manual_contract_requires_summary():
    with pytest.raises(ValidationError):
        ManualAnswer.model_validate({"steps": ["第一步"]})


def test_router_contract_only_allows_known_query_types():
    with pytest.raises(ValidationError):
        RouterLLMOutput.model_validate(
            {"query_type": "quote", "confidence": 0.9}
        )
