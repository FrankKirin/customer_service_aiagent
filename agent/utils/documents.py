"""检索文档、来源信息和引用的纯函数工具。

原则：
- 来源和引用只能从真实 Document.metadata 构造；
- checkpoint 前把 metadata 规范化为基础类型；
- 没有检索结果时返回空列表，不生成占位来源。
"""

import re
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from langchain_core.documents import Document
from pydantic import BaseModel, ValidationError

from ..state import Citation, SourceInfo

SourceType = Literal["policy", "manual"]

_USED_SOURCE_PATTERN = re.compile(r"^[Ss]?(\d+)$")
_MAX_CITATION_TEXT = 500
_MAX_CITATIONS = 5


def _to_primitive(value: Any) -> Any:
    """把 metadata 中的第三方对象递归转换成可序列化值。"""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_primitive(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {str(key): _to_primitive(value) for key, value in metadata.items()}


def normalize_documents(documents: List[Document], max_documents: int = 20) -> List[Document]:
    """规范化 metadata，并按 chunk_id 去重。"""

    normalized: List[Document] = []
    seen: set[tuple[Any, ...]] = set()

    for document in documents:
        if not isinstance(document, Document):
            raise TypeError(f"检索结果必须是 Document，实际为 {type(document).__name__}")

        metadata = _normalize_metadata(document.metadata)
        dedupe_key: tuple[Any, ...] = (
            metadata.get("chunk_id")
            or metadata.get("doc_id")
            or metadata.get("source")
            or document.page_content[:80]
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(
            Document(page_content=document.page_content, metadata=metadata)
        )
        if len(normalized) >= max_documents:
            break
    return normalized


def _coerce_page(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= score <= 1.0:
        return round(score, 4)
    return 0.0


def source_info_from_document(
    document: Document, source_type: SourceType
) -> Optional[SourceInfo]:
    """从真实文档 metadata 构造 SourceInfo。"""

    metadata = document.metadata
    raw_source_info = metadata.get("source_info")

    if isinstance(raw_source_info, SourceInfo):
        return raw_source_info
    if isinstance(raw_source_info, dict):
        try:
            return SourceInfo.model_validate(raw_source_info)
        except ValidationError:
            pass

    source_id = (
        metadata.get("document_id")
        or metadata.get("source")
        or metadata.get("file_name")
        or metadata.get("doc_id")
    )
    if not source_id:
        return None

    try:
        return SourceInfo(
            source_id=str(source_id),
            source_type=source_type,
            section=metadata.get("section"),
            page=_coerce_page(metadata.get("page")),
            url=metadata.get("url") or metadata.get("source_url"),
            relevance_score=_coerce_score(metadata.get("relevance_score")),
        )
    except (ValidationError, TypeError, ValueError):
        return None

def build_source_info(
    documents: List[Document], source_type: SourceType
) -> List[SourceInfo]:
    """为检索结果构造来源列表，跳过元数据不完整的文档。"""

    sources: List[SourceInfo] = []
    for document in documents:
        source = source_info_from_document(document, source_type)
        if source is not None:
            sources.append(source)
    return sources


def _parse_used_source_indexes(
    used_sources: List[str], document_count: int
) -> List[int]:
    indexes: List[int] = []
    for item in used_sources:
        if not isinstance(item, str):
            continue
        match = _USED_SOURCE_PATTERN.match(item.strip())
        if not match:
            continue
        index = int(match.group(1)) - 1
        if 0 <= index < document_count and index not in indexes:
            indexes.append(index)
    return indexes


def build_citations(
    used_sources: List[str],
    documents: List[Document],
    source_type: SourceType,
    fallback_count: int = 3,
) -> List[Citation]:
    """根据模型返回的 S1/S2 编号，从真实文档生成引用。

    fallback_count>0 时，如果模型没有返回有效编号，使用本次实际提供给模型的
    前几个文档作为引用；这些仍然是真实检索结果，不是模型编造的内容。
    """

    if not documents:
        return []

    indexes = _parse_used_source_indexes(used_sources, len(documents))
    if not indexes and fallback_count > 0:
        indexes = list(range(min(fallback_count, len(documents))))

    citations: List[Citation] = []
    seen: set[tuple[Any, ...]] = set()
    for index in indexes:
        document = documents[index]
        source = source_info_from_document(document, source_type)
        if source is None:
            continue
        dedupe_key = (source.source_id, source.section, source.page)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        text = document.page_content.strip()
        if not text:
            continue
        citations.append(
            Citation(
                text=text[:_MAX_CITATION_TEXT],
                source=source,
                start_char=0,
                end_char=min(len(text), _MAX_CITATION_TEXT),
            )
        )
        if len(citations) >= _MAX_CITATIONS:
            break
    return citations


def calculate_retrieval_confidence(documents: List[Document]) -> float:
    """只用真实的 relevance_score 计算平均检索置信度。"""

    scores: List[float] = []
    for document in documents:
        score = _coerce_score(document.metadata.get("relevance_score"))
        if score > 0:
            scores.append(score)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def documents_to_prompt_context(
    documents: List[Document], max_chars_per_document: int = 1200
) -> str:
    """把真实文档格式化为带 S1/S2 编号的提示词上下文。"""

    blocks: List[str] = []
    for index, document in enumerate(documents, start=1):
        metadata = document.metadata
        lines = [f"[S{index}]"]
        if metadata.get("source"):
            lines.append(f"来源: {metadata['source']}")
        if metadata.get("file_name"):
            lines.append(f"文件: {metadata['file_name']}")
        if metadata.get("section"):
            lines.append(f"章节: {metadata['section']}")
        if metadata.get("page") is not None:
            lines.append(f"页码: {metadata['page']}")
        content = document.page_content.strip()
        lines.append(f"内容: {content[:max_chars_per_document]}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

