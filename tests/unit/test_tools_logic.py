"""工具纯逻辑单元测试: 不访问向量库, 也不调用 embedding 接口"""

from datetime import datetime

import pytest
from langchain_core.documents import Document

from agent.tools.clarification_tool import FIELD_PROMPTS, _build_question
from agent.tools.confidence_tool import (
    RETRIEVAL_WEIGHT,
    _assess_confidence,
    _calculate_retrieval_score,
)
from agent.tools.manual_retriever import _build_filter_conditions as build_manual_filters
from agent.tools.policy_retriever import _build_filter_conditions as build_policy_filters


# ------------------------------------------------------------------ 政策过滤条件
def test_policy_filter_is_none_when_no_condition_given():
    """没有任何过滤维度时不产生 where 条件"""
    assert build_policy_filters(None, None, None) is None


def test_policy_filter_combines_dimensions_with_and():
    """多个过滤维度用 $and 组合"""
    result = build_policy_filters("家用电器", "CN", None)
    assert result == {"$and": [{"product_line": "家用电器"}, {"region": "CN"}]}


def test_policy_filter_maps_date_range_to_effective_date():
    """时间范围映射成 effective_date 的区间比较"""
    result = build_policy_filters(None, None, (datetime(2024, 1, 1), datetime(2024, 12, 31)))
    assert result == {"effective_date": {"$gte": "2024-01-01", "$lte": "2024-12-31"}}


def test_policy_filter_rejects_reversed_date_range():
    """开始时间晚于结束时间应直接报错"""
    with pytest.raises(ValueError):
        build_policy_filters(None, None, (datetime(2024, 12, 31), datetime(2024, 1, 1)))


def test_policy_filter_rejects_incomplete_date_range():
    """时间范围必须是两个元素"""
    with pytest.raises(ValueError):
        build_policy_filters(None, None, (datetime(2024, 1, 1),))


# ------------------------------------------------------------------ 说明书过滤条件
def test_manual_filter_supports_single_dimension():
    """单一过滤维度直接返回, 不再包一层 $and"""
    assert build_manual_filters("N.30-2-31", None) == {"product_model": "N.30-2-31"}


def test_manual_filter_combines_dimensions_with_and():
    result = build_manual_filters("N.30-2-31", "troubleshooting")
    assert result == {
        "$and": [
            {"product_model": "N.30-2-31"},
            {"issue_type": "troubleshooting"},
        ]
    }


# -------------------------------------------------------------------- 置信度评估
def _doc_with_score(score: float) -> Document:
    return Document(page_content="文档片段", metadata={"relevance_score": score})


def test_retrieval_score_is_average_of_real_scores():
    docs = [_doc_with_score(0.8), _doc_with_score(0.6)]
    assert _calculate_retrieval_score(docs) == pytest.approx(0.7)


def test_retrieval_score_is_zero_when_metadata_has_no_score():
    """没有 relevance_score 时返回0, 不填充任何假设值"""
    docs = [Document(page_content="文档片段", metadata={})]
    assert _calculate_retrieval_score(docs) == 0.0


def test_retrieval_score_ignores_invalid_and_out_of_range_values():
    docs = [
        _doc_with_score(0.5),
        Document(page_content="文档片段", metadata={"relevance_score": "非法值"}),
        _doc_with_score(3.0),
    ]
    assert _calculate_retrieval_score(docs) == pytest.approx(0.5)


def test_overall_confidence_uses_retrieval_score_only_when_validation_missing():
    result = _assess_confidence("查询", [_doc_with_score(0.8), _doc_with_score(0.6)], None)
    assert result["overall_confidence"] == pytest.approx(0.7)
    assert result["validation_score"] is None
    assert result["should_fallback"] is False


def test_overall_confidence_applies_validation_weight():
    result = _assess_confidence("查询", [_doc_with_score(1.0)], 0.0)
    assert result["overall_confidence"] == pytest.approx(RETRIEVAL_WEIGHT)


def test_overall_confidence_is_zero_and_falls_back_without_documents():
    result = _assess_confidence("查询", [], None)
    assert result["overall_confidence"] == 0.0
    assert result["doc_count"] == 0
    assert result["should_fallback"] is True


def test_assess_confidence_rejects_empty_query():
    with pytest.raises(ValueError):
        _assess_confidence("", [], None)


# -------------------------------------------------------------------- 信息澄清
def test_build_question_uses_template_of_known_field():
    assert _build_question(["product_model"]) == FIELD_PROMPTS["product_model"]


def test_build_question_asks_for_unknown_field_name():
    assert _build_question(["颜色"]) == "请问颜色是什么？"


def test_build_question_rejects_empty_field_list():
    with pytest.raises(ValueError):
        _build_question([])
