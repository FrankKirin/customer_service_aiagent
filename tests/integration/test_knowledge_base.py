"""知识库召回冒烟测试。

默认跳过；构建完真实集合后执行：
    RUN_KNOWLEDGE_BASE_EVAL=1 python -m pytest tests/integration/test_knowledge_base.py -q

仅用 golden_set.jsonl 中的真实题目，不生成任何假数据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from rag.vector_store import get_vector_store_manager

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SET = PROJECT_ROOT / "rag" / "eval" / "golden_set.jsonl"


def _load_golden_set() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with GOLDEN_SET.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _has_number_or_model(keyword: str) -> bool:
    return any(char.isdigit() or char.isascii() and char.isalpha() for char in keyword)


def _section_matches(document_section: str | None, source_section: str | None) -> bool:
    if not document_section or not source_section:
        return False
    leaf_section = document_section.split("/")[-1]
    return leaf_section == source_section or source_section in document_section


def _keywords_match(content: str, keywords: list[str]) -> bool:
    required_count = min(2, len(keywords))
    matched = [keyword for keyword in keywords if keyword in content]
    # 数字/型号类关键词必须命中，避免只有泛化词被算作召回。
    must_match = [keyword for keyword in keywords if _has_number_or_model(keyword)]
    return len(matched) >= required_count and all(
        keyword in content for keyword in must_match
    )


def test_policy_recall_at_4_meets_baseline():
    if os.getenv("RUN_KNOWLEDGE_BASE_EVAL") != "1":
        pytest.skip("设置 RUN_KNOWLEDGE_BASE_EVAL=1 并先构建知识库后执行")

    manager = get_vector_store_manager()
    if manager.get_collection_count("policy_docs") == 0:
        pytest.skip("policy_docs 集合为空，请先运行构建脚本")

    rows = _load_golden_set()
    answerable = [
        row
        for row in rows
        if row["answerable"] and not row.get("requires_manual") and row.get("source_section")
    ]
    hits = 0
    for row in answerable:
        documents = manager.search_policy_documents(row["question"], top_k=4)
        hit = any(
            _section_matches(document.metadata.get("section"), row["source_section"])
            and _keywords_match(document.page_content, row["retrieval_keywords"])
            for document in documents
        )
        hits += int(hit)
        print(
            f"{row['id']} hit={hit} top_sections="
            f"{[document.metadata.get('section') for document in documents]}"
        )

    recall = hits / len(answerable)
    print(f"policy Recall@4: {hits}/{len(answerable)} = {recall:.2%}")
    assert recall >= 0.80


def test_unanswerable_questions_are_recorded_with_top4():
    """不可回答样本只记录 top-4，不计算召回。"""
    if os.getenv("RUN_KNOWLEDGE_BASE_EVAL") != "1":
        pytest.skip("设置 RUN_KNOWLEDGE_BASE_EVAL=1 并先构建知识库后执行")

    manager = get_vector_store_manager()
    if manager.get_collection_count("policy_docs") == 0:
        pytest.skip("policy_docs 集合为空，请先运行构建脚本")

    rows = [row for row in _load_golden_set() if not row["answerable"] and not row.get("requires_manual")]
    for row in rows:
        documents = manager.search_policy_documents(row["question"], top_k=4)
        print(
            f"{row['id']} top4="
            f"{[(document.metadata.get('section'), document.metadata.get('relevance_score')) for document in documents]}"
        )


def test_policy_metadata_filters_return_non_empty():
    """验收真实存在的元数据过滤：policy_type 与 effective_date 区间。"""
    if os.getenv("RUN_KNOWLEDGE_BASE_EVAL") != "1":
        pytest.skip("设置 RUN_KNOWLEDGE_BASE_EVAL=1 并先构建知识库后执行")

    manager = get_vector_store_manager()
    if manager.get_collection_count("policy_docs") == 0:
        pytest.skip("policy_docs 集合为空，请先运行构建脚本")

    by_type = manager.search_policy_documents(
        "退货", filter_conditions={"policy_type": "refund"}, top_k=3
    )
    by_date = manager.search_policy_documents(
        "退货",
        filter_conditions={"effective_date": {"$gte": "2026-01-01", "$lte": "2026-12-31"}},
        top_k=3,
    )
    assert by_type
    assert by_date


def test_manual_gap_questions_record_top4():
    """说明书缺位样本不计算召回，只记录 top-4 供阶段4使用。"""
    if os.getenv("RUN_KNOWLEDGE_BASE_EVAL") != "1":
        pytest.skip("设置 RUN_KNOWLEDGE_BASE_EVAL=1 并先构建知识库后执行")

    manager = get_vector_store_manager()
    if manager.get_collection_count("manual_docs") == 0:
        pytest.skip("manual_docs 集合为空，请先运行构建脚本")

    rows = [row for row in _load_golden_set() if row.get("requires_manual")]
    for row in rows:
        documents = manager.search_manual_documents(row["question"], top_k=4)
        print(
            f"{row['id']} top4="
            f"{[(document.metadata.get('page'), document.metadata.get('relevance_score')) for document in documents]}"
        )
