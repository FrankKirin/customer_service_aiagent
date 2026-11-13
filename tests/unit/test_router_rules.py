"""规则路由测试，题目全部来自 rag/eval/golden_set.jsonl。"""

import json
from pathlib import Path
from typing import Any, Dict

from agent.routing_rules import classify_by_rules, extract_slots, looks_ambiguous

GOLDEN_SET = Path(__file__).resolve().parents[2] / "rag" / "eval" / "golden_set.jsonl"


def _load_golden_set() -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    with GOLDEN_SET.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            rows[row["id"]] = row
    return rows


def test_strong_policy_questions_are_routed_without_llm():
    rows = _load_golden_set()
    policy_ids = (
        "keg-AS-001",
        "keg-AS-003",
        "keg-AS-004",
        "keg-AS-005",
        "keg-AS-006",
        "keg-AS-007",
        "keg-AS-008",
        "keg-AS-010",
        "keg-AS-011",
        "keg-AS-012",
        "keg-AS-013",
        "keg-AS-014",
        "keg-AS-021",
        "keg-AS-022",
        "keg-AS-024",
    )

    for row_id in policy_ids:
        result = classify_by_rules(rows[row_id]["question"])
        assert result is not None, row_id
        assert result.query_type == "policy", row_id
        assert result.confidence >= 0.75


def test_manual_gap_questions_are_routed_to_manual():
    rows = _load_golden_set()

    for row_id in ("keg-AS-018", "keg-AS-019", "keg-AS-020"):
        result = classify_by_rules(rows[row_id]["question"])
        assert result is not None, row_id
        assert result.query_type == "manual", row_id


def test_ambiguous_questions_are_not_forced_into_subgraph():
    rows = _load_golden_set()

    for row_id in ("keg-AS-023", "keg-AS-025"):
        question = rows[row_id]["question"]
        assert classify_by_rules(question) is None
        assert looks_ambiguous(question) is True


def test_slots_are_extracted_only_from_explicit_text():
    slots = extract_slots("我在门店买的冰箱，收到货第5天发现制冷出了问题", {})

    assert slots["purchase_channel"] == "线下门店"
    assert slots["received_days"] == 5
    assert "product_model" not in slots
    assert "region" not in slots


def test_slot_extraction_preserves_existing_unknown_values():
    existing = {"product_model": "用户之前提供的型号"}
    slots = extract_slots("收到第3天发现不制冷", existing)

    assert slots["product_model"] == "用户之前提供的型号"
    assert slots["received_days"] == 3
    assert slots["issue_type"] == "troubleshooting"
