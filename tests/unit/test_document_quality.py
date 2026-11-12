"""页面质量规则单元测试（使用真实 PDF 页面文本）。"""

from pathlib import Path

import pymupdf
import pytest

from rag.document_quality import (
    assess_page_quality,
    decide_page_indexing,
    is_garbled,
    is_page_num_only,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANUAL_FILE = PROJECT_ROOT / "raw_data" / "manual_docs" / "家用冰箱通用说明书_N.30-2-31.pdf"


@pytest.fixture(scope="module")
def page_texts():
    if not MANUAL_FILE.is_file():
        pytest.skip("真实说明书文档不存在")
    document = pymupdf.open(str(MANUAL_FILE))
    try:
        return {page.number + 1: page.get_text("text") or "" for page in document}
    finally:
        document.close()


def test_page_num_only_covers_title_plus_page_number(page_texts):
    assert is_page_num_only(page_texts[8])
    assert is_page_num_only(page_texts[31])


def test_toc_symbols_are_not_treated_as_garbled(page_texts):
    assert not is_garbled(page_texts[2])
    quality = assess_page_quality(page_texts[2], page=2)
    assert "garbled" not in quality.issues


def test_assess_page_quality_records_expected_issues(page_texts):
    assert "too_short" in assess_page_quality(
        page_texts[1], page=1, has_images=False
    ).issues
    assert "page_num_only" in assess_page_quality(
        page_texts[15], page=15, has_images=False
    ).issues


def test_too_short_page_needs_table_or_caption_to_be_indexed():
    quality = assess_page_quality("这是简短页面的一段正文", page=1, has_images=False)

    indexed, reason = decide_page_indexing(quality)
    assert indexed is False
    assert reason
    indexed_with_table, _ = decide_page_indexing(quality, has_table=True)
    assert indexed_with_table is True


def test_blank_page_is_not_indexed():
    quality = assess_page_quality("", page=1, has_images=False)

    assert "blank" in quality.issues
    indexed, reason = decide_page_indexing(quality)
    assert indexed is False
    assert reason == "空白页，无有效文本"


def test_ocr_low_confidence_is_recorded_and_not_indexed():
    quality = assess_page_quality(
        "可识别的正文内容足够长一些", page=8, source="ocr", ocr_confidence=0.45
    )

    assert "low_ocr_confidence" in quality.issues
    assert "needs_review" in quality.issues
    indexed, _ = decide_page_indexing(quality)
    assert indexed is False
