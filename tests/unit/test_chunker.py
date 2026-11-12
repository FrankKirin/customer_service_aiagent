"""章节感知切块单元测试。"""

from pathlib import Path

import pytest

from rag.chunker import chunk_by_chars, chunk_by_sections
from rag.document_parser import DocumentParser, ParsedBlock
from rag.document_processor import DocumentProcessor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = PROJECT_ROOT / "raw_data" / "policy_docs" / "售后服务政策.md"
MANUAL_FILE = PROJECT_ROOT / "raw_data" / "manual_docs" / "家用冰箱通用说明书_N.30-2-31.pdf"


@pytest.fixture(scope="module")
def policy_parsed():
    if not POLICY_FILE.is_file():
        pytest.skip("真实政策文档不存在")
    return DocumentParser().parse_policy_document(POLICY_FILE)


@pytest.fixture(scope="module")
def manual_parsed():
    if not MANUAL_FILE.is_file():
        pytest.skip("真实说明书文档不存在")
    return DocumentParser().parse_manual_document(MANUAL_FILE)


def test_policy_chunks_keep_section_and_stay_within_limit(policy_parsed):
    chunks = chunk_by_sections(policy_parsed.blocks)

    assert chunks
    assert all(chunk.section for chunk in chunks)
    assert all(chunk.page is None for chunk in chunks)
    assert all(chunk.char_count == len(chunk.content) for chunk in chunks)
    assert all(len(chunk.content) <= 500 for chunk in chunks)
    assert all(len(chunk.content.strip()) >= 10 for chunk in chunks)


def test_manual_chunks_keep_page_and_do_not_cross_pages(manual_parsed):
    chunks = chunk_by_sections(manual_parsed.blocks)

    assert chunks
    assert all(chunk.page is not None for chunk in chunks)
    assert all(len(chunk.content) <= 500 for chunk in chunks)
    assert all(len(chunk.content.strip()) >= 10 for chunk in chunks)
    # 同一切块只能来自一个页面，页码引用才准确。
    assert len({chunk.page for chunk in chunks}) > 1


def test_table_blocks_are_preserved_and_long_table_repeats_header(manual_parsed):
    table_blocks = [block for block in manual_parsed.blocks if block.block_type == "table"]
    assert table_blocks
    table_block = max(table_blocks, key=lambda block: len(block.text))
    chunks = chunk_by_sections([table_block])

    assert chunks
    for chunk in chunks:
        assert chunk.block_type == "table"
        assert chunk.content.startswith("| ")
        assert len(chunk.content) <= 500


def test_short_adjacent_blocks_are_merged():
    blocks = (
        ParsedBlock(text="短块甲内容", page=1, section="同章节"),
        ParsedBlock(text="短块乙内容", page=1, section="同章节"),
    )

    chunks = chunk_by_sections(blocks, min_chars=20)

    assert len(chunks) == 1
    assert "短块甲" in chunks[0].content
    assert "短块乙" in chunks[0].content


def test_invalid_chunk_parameters_raise():
    with pytest.raises(ValueError):
        chunk_by_sections([ParsedBlock(text="正文足够长一些")], max_chars=0)
    with pytest.raises(ValueError):
        chunk_by_sections([ParsedBlock(text="正文足够长一些")], max_chars=100, overlap=100)
    with pytest.raises(ValueError):
        chunk_by_sections([ParsedBlock(text="正文足够长一些")], chunk_size=100, max_chars=50)


def test_stable_chunk_ids_and_content_across_repeated_builds(policy_parsed):
    processor = DocumentProcessor()
    first = processor._to_documents(policy_parsed)
    second = processor._to_documents(policy_parsed)

    first_payload = [
        (document.metadata["chunk_id"], document.page_content) for document in first
    ]
    second_payload = [
        (document.metadata["chunk_id"], document.page_content) for document in second
    ]
    assert first_payload == second_payload
    assert len({document.metadata["chunk_id"] for document in first}) == len(first)
    for document in first:
        metadata = document.metadata
        assert metadata["chunk_id"].endswith(f"::{metadata['chunk_index']}")


def test_chunk_by_chars_keeps_legacy_behavior():
    chunks = chunk_by_chars("0123456789", chunk_size=4, overlap=1)

    assert chunks == ["0123", "3456", "6789"]
