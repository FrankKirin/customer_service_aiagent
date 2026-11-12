"""rag.document_processor 的单元测试

直接解析 raw_data 下的真实文档来验证解析/切块/元数据逻辑, 不构造任何业务假数据。
真实文档缺失时自动跳过, 不会用示例数据替代。
"""

from datetime import date
from pathlib import Path

import pytest

from rag.document_processor import (
    DEFAULT_MANUAL_DIR,
    DEFAULT_POLICY_DIR,
    MANUAL_FRONT_MATTER_MAPPING,
    POLICY_FRONT_MATTER_MAPPING,
    SUPPORTED_SUFFIXES,
    DocumentProcessor,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = PROJECT_ROOT / "raw_data" / "policy_docs"
MANUAL_DIR = PROJECT_ROOT / "raw_data" / "manual_docs"

# 允许出现在向量元数据里的字段: 来自文件本身(front matter/PDF信息)或解析过程的结构字段
ALLOWED_METADATA_KEYS = (
    set(POLICY_FRONT_MATTER_MAPPING.values())
    | set(MANUAL_FRONT_MATTER_MAPPING.values())
    | {
        "source_type",
        "file_name",
        "file_path",
        "source",
        "chunk_index",
        "doc_id",
        "chunk_id",
        "block_type",
        "page",
        "pdf_title",
        "section",
        "table_index",
        "image_path",
        "image_hash",
        "effective_date_num",
    }
)


@pytest.fixture(scope="module")
def processor() -> DocumentProcessor:
    """只使用解析能力, 不初始化向量库"""
    parser = DocumentProcessor()
    assert parser._vector_store is None  # 确认构造过程没有触发向量库初始化
    return parser


# ---------------------------------------------------------------- 文件发现
def test_discover_files_raises_when_directory_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        DocumentProcessor.discover_files(str(tmp_path / "not_exists"))


@pytest.mark.skipif(not POLICY_DIR.is_dir(), reason="raw_data/policy_docs 不存在")
def test_discover_files_returns_only_supported_files():
    files = DocumentProcessor.discover_files(DEFAULT_POLICY_DIR)
    assert files, "政策文档目录里没有可解析文件"
    for file_path in files:
        assert Path(file_path).suffix.lower() in SUPPORTED_SUFFIXES


# ------------------------------------------------------- front matter 元数据映射
def test_map_front_matter_only_keeps_provided_fields():
    """front matter 缺失的字段不应被补上默认值"""
    mapping = DocumentProcessor._map_front_matter({"region": "CN"}, POLICY_FRONT_MATTER_MAPPING)
    assert mapping == {"region": "CN"}


def test_map_front_matter_skips_empty_values():
    mapping = DocumentProcessor._map_front_matter({"region": "", "doc_id": None}, POLICY_FRONT_MATTER_MAPPING)
    assert mapping == {}


def test_map_front_matter_converts_date_like_value_to_string():
    mapping = DocumentProcessor._map_front_matter(
        {"effective_date": date(2026, 1, 1)}, POLICY_FRONT_MATTER_MAPPING
    )
    assert mapping == {"effective_date": "2026-01-01"}


def test_map_front_matter_ignores_keys_outside_mapping():
    mapping = DocumentProcessor._map_front_matter({"unknown_key": "值"}, POLICY_FRONT_MATTER_MAPPING)
    assert mapping == {}


# ------------------------------------------------------------- 真实文件解析
@pytest.mark.skipif(not POLICY_DIR.is_dir(), reason="raw_data/policy_docs 不存在")
def test_policy_documents_are_chunked_with_real_metadata(processor):
    policy_files = DocumentProcessor.discover_files(DEFAULT_POLICY_DIR)
    documents = []
    for file_path in policy_files:
        documents.extend(processor._build_policy_documents(file_path))

    assert documents, "政策文档没有解析出任何块"
    for document in documents:
        assert document.page_content.strip()
        assert document.metadata["source_type"] == "policy"
        # 元数据必须全部来自真实文件或解析结构, 不允许出现凭空构造的业务字段
        assert set(document.metadata) <= ALLOWED_METADATA_KEYS

    doc_ids = [document.metadata["doc_id"] for document in documents]
    assert len(doc_ids) == len(set(doc_ids)), "doc_id 出现重复"


@pytest.mark.skipif(not MANUAL_DIR.is_dir(), reason="raw_data/manual_docs 不存在")
def test_manual_pdf_documents_keep_page_metadata(processor):
    manual_files = DocumentProcessor.discover_files(DEFAULT_MANUAL_DIR)
    documents = []
    for file_path in manual_files:
        documents.extend(processor._build_manual_documents(file_path))

    assert documents, "说明书文档没有解析出任何块"
    pages = {document.metadata.get("page") for document in documents}
    assert pages and None not in pages, "PDF 解析结果缺少页码"
    for document in documents:
        assert document.metadata["source_type"] == "manual"
        assert set(document.metadata) <= ALLOWED_METADATA_KEYS


def test_build_policy_documents_rejects_unsupported_suffix(processor):
    with pytest.raises(ValueError):
        processor._build_policy_documents("raw_data/policy_docs/unsupported.docx")


def test_build_manual_documents_rejects_unsupported_suffix(processor):
    with pytest.raises(ValueError):
        processor._build_manual_documents("raw_data/manual_docs/unsupported.docx")
