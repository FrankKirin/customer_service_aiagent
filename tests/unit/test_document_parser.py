"""解析器单元测试。

真实文档作为输入；异常输入只使用空文件/损坏字节/临时结构文件，不构造业务假文档。
"""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from rag.document_parser import DocumentParser, POLICY_FRONT_MATTER_MAPPING

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = PROJECT_ROOT / "raw_data" / "policy_docs"
MANUAL_DIR = PROJECT_ROOT / "raw_data" / "manual_docs"
POLICY_FILE = POLICY_DIR / "售后服务政策.md"
MANUAL_FILE = MANUAL_DIR / "家用冰箱通用说明书_N.30-2-31.pdf"

POLICY_AVAILABLE = POLICY_FILE.is_file()
MANUAL_AVAILABLE = MANUAL_FILE.is_file()


def test_front_matter_trailing_slash_comment_is_tolerated():
    if not POLICY_AVAILABLE:
        pytest.skip("真实政策文档不存在")

    parsed = DocumentParser().parse_policy_document(POLICY_FILE)

    assert parsed.doc_metadata["status"] == "active"
    assert parsed.doc_metadata["doc_type"] == "after_sales_policy"
    assert parsed.doc_metadata["policy_type"] == "refund"
    assert parsed.doc_metadata["effective_date_num"] == 20260101
    # front matter 的 expiration_date 为 null，按约定不写入元数据。
    assert "expiration_date" not in parsed.doc_metadata


def test_markdown_section_path_contains_heading_hierarchy():
    if not POLICY_AVAILABLE:
        pytest.skip("真实政策文档不存在")

    parsed = DocumentParser().parse_policy_document(POLICY_FILE)
    sections = {block.section for block in parsed.blocks if block.section}

    assert "服务政策/购买韩电产品可享受的权益" in sections
    assert any(section.endswith("退换货规定") for section in sections)
    assert any(section.endswith("退换货流程") for section in sections)


def test_manual_pdf_records_all_pages_and_page_quality():
    if not MANUAL_AVAILABLE:
        pytest.skip("真实说明书文档不存在")

    parsed = DocumentParser().parse_manual_document(MANUAL_FILE)

    assert len(parsed.page_qualities) == 65
    assert all(block.page is not None for block in parsed.blocks)
    issues_by_page = {quality.page: set(quality.issues) for quality in parsed.page_qualities}
    assert "too_short" in issues_by_page[1]
    for page in range(8, 18):
        assert "page_num_only" in issues_by_page[page]
    for page in range(31, 34):
        assert "page_num_only" in issues_by_page[page]
    assert any(block.block_type == "table" for block in parsed.blocks)


def test_unknown_suffix_raises_value_error(tmp_path):
    path = tmp_path / "unsupported.docx"
    path.write_bytes(b"not a supported document")

    with pytest.raises(ValueError, match="不支持的文档格式"):
        DocumentParser().parse_policy_document(path)


def test_empty_text_file_raises_value_error(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="文档没有可解析内容"):
        DocumentParser().parse_policy_document(path)


def test_empty_pdf_raises_value_error(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")

    with pytest.raises(ValueError, match="PDF 解析失败"):
        DocumentParser().parse_manual_document(path)


def test_encrypted_pdf_raises_value_error(tmp_path):
    path = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("test-password")
    with path.open("wb") as file:
        writer.write(file)

    with pytest.raises(ValueError, match="PDF 已加密"):
        DocumentParser().parse_manual_document(path)


def test_broken_front_matter_raises_value_error(tmp_path):
    path = tmp_path / "broken.md"
    path.write_text("---\ntitle: [unclosed\n---\n正文", encoding="utf-8")

    with pytest.raises(ValueError, match="YAML front matter 解析失败"):
        DocumentParser().parse_policy_document(path)


def test_front_matter_mapping_does_not_override_real_policy_type():
    from datetime import date

    from rag.document_parser import map_front_matter

    mapped = map_front_matter(
        {
            "doc_type": "after_sales_policy",
            "policy_type": "refund",
            "effective_date": date(2026, 1, 1),
        },
        POLICY_FRONT_MATTER_MAPPING,
    )

    assert mapped == {
        "doc_type": "after_sales_policy",
        "policy_type": "refund",
        "effective_date": "2026-01-01",
    }
