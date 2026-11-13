"""检索文档与引用工具测试。"""

from langchain_core.documents import Document

from agent.state import SourceInfo
from agent.utils.documents import (
    build_citations,
    normalize_documents,
    source_info_from_document,
)


def _document() -> Document:
    return Document(
        page_content="消费者自收到产品之日起7日内，发生性能故障，消费者可以选择退货、换货或修理。",
        metadata={
            "document_id": "keg_after_sales_policy_2026",
            "source": "售后服务政策",
            "section": "服务政策/如何退、换货？/退换货规定",
            "relevance_score": 0.9,
            "chunk_id": "keg_after_sales_policy_2026::0::2",
            "source_info": SourceInfo(
                source_id="keg_after_sales_policy_2026",
                source_type="policy",
                section="服务政策/如何退、换货？/退换货规定",
                relevance_score=0.9,
            ),
        },
    )


def test_normalize_documents_converts_source_info_and_deduplicates():
    documents = normalize_documents([_document(), _document()])

    assert len(documents) == 1
    assert isinstance(documents[0].metadata["source_info"], dict)
    assert documents[0].metadata["chunk_id"] == "keg_after_sales_policy_2026::0::2"


def test_build_citations_uses_only_valid_s_indexes():
    document = _document()
    citations = build_citations(["S9", "S1"], [document], "policy")

    assert len(citations) == 1
    assert citations[0].source.source_id == "keg_after_sales_policy_2026"
    assert "7日内" in citations[0].text


def test_build_citations_falls_back_to_real_documents_when_indexes_invalid():
    citations = build_citations(["S99"], [_document()], "policy", fallback_count=1)

    assert len(citations) == 1


def test_build_citations_returns_empty_without_documents():
    assert build_citations(["S1"], [], "policy") == []


def test_source_info_only_uses_real_metadata():
    document = _document()
    source = source_info_from_document(document, "policy")

    assert source is not None
    assert source.source_id == "keg_after_sales_policy_2026"
    assert source.relevance_score == 0.9
