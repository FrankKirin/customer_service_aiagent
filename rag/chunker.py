"""文档切块。

``chunk_by_chars`` 保留为通用字符切块工具；知识库构建使用
``chunk_by_sections``，按 (section, page) 分组并保留表格/安全提示块。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from rag.document_parser import BlockType, ParsedBlock, Scalar

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50
DEFAULT_MIN_CHARS = 120
MIN_MEANINGFUL_CHARS = 10

PROTECTED_KEYWORDS = ("警告", "注意", "切勿", "必须", "禁止")


@dataclass(frozen=True)
class Chunk:
    """切块结果；chunk_index 由最终输出顺序统一编号。"""

    content: str
    section: str | None
    page: int | None
    block_type: BlockType
    chunk_index: int
    char_count: int
    metadata: Mapping[str, Scalar] = field(default_factory=dict)


@dataclass(frozen=True)
class _ChunkDraft:
    content: str
    section: str | None
    page: int | None
    block_type: BlockType
    metadata: Mapping[str, Scalar] = field(default_factory=dict)



def chunk_by_chars(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """将文本按字符数分割成块（保留原有通用接口）。"""
    if not text:
        raise ValueError("输入文本不能为空")
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须为正整数，当前值: {chunk_size}")
    if overlap < 0:
        raise ValueError(f"overlap 不能为负数，当前值: {overlap}")
    if overlap >= chunk_size:
        raise ValueError(
            f"overlap 必须小于 chunk_size，当前值: overlap={overlap}, chunk_size={chunk_size}"
        )

    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def chunk_by_sections(
    blocks: Iterable[ParsedBlock],
    max_chars: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    min_chars: int = DEFAULT_MIN_CHARS,
    *,
    chunk_size: int | None = None,
) -> list[Chunk]:
    """按 (section, page) 分组切块。

    - 正文按中文句末标点切分，并保留 overlap；
    - 表格/图片 caption/含安全提示关键词的块整块保留；
    - 表格超长时按行拆分，并重复表头；
    - 过短正文块优先与同组相邻正文块合并。

    ``chunk_size`` 是旧调用方的别名，内部统一使用 ``max_chars``。
    """
    if chunk_size is not None:
        if max_chars != DEFAULT_CHUNK_SIZE and max_chars != chunk_size:
            raise ValueError("max_chars 与 chunk_size 同时提供且值不一致")
        max_chars = chunk_size

    _validate_chunk_options(max_chars, overlap, min_chars)

    groups = _group_consecutive_blocks(blocks)
    raw_chunks: list[_ChunkDraft] = []
    for group in groups:
        raw_chunks.extend(
            _chunk_group(
                group,
                chunk_size=max_chars,
                overlap=overlap,
                min_chars=min_chars,
            )
        )

    meaningful_chunks = [
        chunk
        for chunk in raw_chunks
        if _effective_length(chunk.content) >= MIN_MEANINGFUL_CHARS
    ]
    return [
        Chunk(
            content=chunk.content,
            section=chunk.section,
            page=chunk.page,
            block_type=chunk.block_type,
            chunk_index=index,
            char_count=len(chunk.content),
            metadata=dict(chunk.metadata),
        )
        for index, chunk in enumerate(meaningful_chunks)
    ]


def _validate_chunk_options(chunk_size: int, overlap: int, min_chars: int) -> None:
    if chunk_size <= 0:
        raise ValueError(f"chunk_size 必须为正整数，当前值: {chunk_size}")
    if overlap < 0:
        raise ValueError(f"overlap 不能为负数，当前值: {overlap}")
    if overlap >= chunk_size:
        raise ValueError(
            f"overlap 必须小于 chunk_size，当前值: overlap={overlap}, chunk_size={chunk_size}"
        )
    if min_chars < 0:
        raise ValueError(f"min_chars 不能为负数，当前值: {min_chars}")


def _group_consecutive_blocks(blocks: Iterable[ParsedBlock]) -> list[list[ParsedBlock]]:
    groups: list[list[ParsedBlock]] = []
    current: list[ParsedBlock] = []
    current_key: tuple[str | None, int | None] | None = None

    for block in blocks:
        if not block.text.strip():
            continue
        block_key = (block.section, block.page)
        if current and block_key != current_key:
            groups.append(current)
            current = []
        current.append(block)
        current_key = block_key

    if current:
        groups.append(current)
    return groups


def _chunk_group(
    group: list[ParsedBlock],
    *,
    chunk_size: int,
    overlap: int,
    min_chars: int,
) -> list[_ChunkDraft]:
    section = group[0].section
    page = group[0].page
    chunks: list[_ChunkDraft] = []
    text_buffer: list[str] = []
    buffer_metadata: dict[str, Scalar] = {}

    def flush_text_buffer() -> None:
        nonlocal text_buffer, buffer_metadata
        text = "\n".join(text_buffer).strip()
        if text:
            chunks.extend(
                _ChunkDraft(
                    content=piece,
                    section=section,
                    page=page,
                    block_type="text",
                    metadata=dict(buffer_metadata),
                )
                for piece in _split_text(text, chunk_size=chunk_size, overlap=overlap)
            )
        text_buffer = []
        buffer_metadata = {}

    for block in group:
        text = block.text.strip()
        if not text:
            continue
        if _is_protected_block(block):
            flush_text_buffer()
            chunks.extend(
                _ChunkDraft(
                    content=piece,
                    section=section,
                    page=page,
                    block_type=block.block_type,
                    metadata=dict(block.metadata),
                )
                for piece in _split_protected_text(
                    text, block_type=block.block_type, chunk_size=chunk_size
                )
            )
        else:
            text_buffer.append(text)
            buffer_metadata.update(block.metadata)

    flush_text_buffer()
    return _merge_short_chunks(chunks, chunk_size=chunk_size, min_chars=min_chars)


def _is_protected_block(block: ParsedBlock) -> bool:
    if block.block_type in {"table", "image_caption"}:
        return True
    return any(keyword in block.text for keyword in PROTECTED_KEYWORDS)


def _merge_short_chunks(
    chunks: list[_ChunkDraft],
    *,
    chunk_size: int,
    min_chars: int,
) -> list[_ChunkDraft]:
    """把同组相邻的过短正文块并入前一块，不跨表格/安全提示块合并。"""
    if not chunks:
        return chunks

    merged = [chunks[0]]
    for current in chunks[1:]:
        previous = merged[-1]
        can_merge = (
            len(current.content) < min_chars
            and current.block_type == "text"
            and previous.block_type == "text"
            and len(previous.content) + 1 + len(current.content) <= chunk_size
        )
        if can_merge:
            metadata = dict(previous.metadata)
            metadata.update(current.metadata)
            merged[-1] = _ChunkDraft(
                content=f"{previous.content}\n{current.content}",
                section=current.section,
                page=current.page,
                block_type="text",
                metadata=metadata,
            )
        else:
            merged.append(current)
    return merged


def _split_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """按句末标点/换行切分，再按长度打包。"""
    segments = [
        segment.strip()
        for segment in re.split(r"(?<=[。！？；!?])\s*|\n+", text)
        if segment.strip()
    ]
    if not segments:
        return []

    chunks: list[str] = []
    current = ""
    for segment in segments:
        if len(segment) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(segment, chunk_size, overlap))
            continue

        if not current:
            current = segment
        elif len(current) + len(segment) <= chunk_size:
            current += segment
        else:
            chunks.append(current)
            prefix = current[-overlap:] if overlap else ""
            candidate = f"{prefix}{segment}"
            current = candidate if len(candidate) <= chunk_size else segment

    if current:
        chunks.append(current)
    return chunks


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text]

    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        pieces.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return pieces



def _split_protected_text(text: str, *, block_type: BlockType, chunk_size: int) -> list[str]:
    """保护块超长时按行拆分；表格重复表头。"""
    if len(text) <= chunk_size:
        return [text]

    if block_type != "table":
        return _split_by_lines(text, chunk_size)

    lines = text.splitlines()
    if not lines:
        return []
    header_count = 2 if len(lines) > 1 and _is_markdown_table_separator(lines[1]) else 1
    header_lines = lines[:header_count]
    body_lines = lines[header_count:]
    header_text = "\n".join(header_lines)

    if len(header_text) >= chunk_size:
        return _hard_split(text, chunk_size, 0)

    chunks: list[str] = []
    current_rows: list[str] = []

    def flush_rows() -> None:
        nonlocal current_rows
        if current_rows:
            chunks.append("\n".join(header_lines + current_rows))
        current_rows = []

    for row in body_lines:
        candidate = "\n".join(header_lines + current_rows + [row])
        if len(candidate) <= chunk_size:
            current_rows.append(row)
            continue

        flush_rows()
        if len(row) + len(header_text) + 1 <= chunk_size:
            current_rows = [row]
            continue

        # 单行本身超过 chunk_size：保留表头后硬切该行。
        available = max(chunk_size - len(header_text) - 1, 1)
        for piece in _hard_split(row, available, 0):
            chunks.append("\n".join(header_lines + [piece]))

    flush_rows()
    return chunks


def _split_by_lines(text: str, chunk_size: int) -> list[str]:
    lines = text.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(line) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(line, chunk_size, 0))
            continue
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks


def _is_markdown_table_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line))


def _effective_length(text: str) -> int:
    return len(re.sub(r"\s", "", text))
