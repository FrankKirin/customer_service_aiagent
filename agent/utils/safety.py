"""从真实说明书原文中抽取安全提示。

安全提示不允许由 LLM 自由生成。这里只做原文句子抽取：
- 命中安全关键词；
- 保留原文语义，仅规范化空白；
- 没有命中时返回空列表，让最终回答不出现安全提示段落。
"""

import re
from typing import List

from langchain_core.documents import Document

SAFETY_KEYWORDS = ("警告", "注意", "切勿", "请勿", "禁止", "必须", "危险", "小心")
_MAX_WARNINGS = 5
_MIN_WARNING_LENGTH = 8

_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？!?；;])")
_CONTINUATION_ENDINGS = ("以免", "以及", "并且", "并", "或者", "或", "和", "、", "：", ":")


def _contains_safety_keyword(text: str) -> bool:
    return any(keyword in text for keyword in SAFETY_KEYWORDS)


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _looks_like_heading(text: str) -> bool:
    stripped = text.strip()
    return any(stripped in {f"{keyword}：", f"{keyword}:"} for keyword in SAFETY_KEYWORDS)


def _split_sentences(line: str) -> List[str]:
    return [part.strip() for part in _SENTENCE_SPLIT_PATTERN.split(line) if part.strip()]


def _logical_lines(text: str) -> List[str]:
    """只合并明确的排版续行，避免把整页无关文字粘成一条警告。

    例如“切勿……以免”和下一行“绝缘失效；”需要合并；
    但“非故障判断”“现象”“原因”之间没有连接词，不能合并。
    """

    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines: List[str] = []
    index = 0
    while index < len(raw_lines):
        line = raw_lines[index]

        # “注意：”“警告：”这种独立标题需要和下一行正文合并
        if _looks_like_heading(line) and index + 1 < len(raw_lines):
            lines.append(f"{line}{raw_lines[index + 1]}")
            index += 2
            continue

        # PDF 自动换行常见于“以免”“以及”“、”“：”结尾的行
        while line.endswith(_CONTINUATION_ENDINGS) and index + 1 < len(raw_lines):
            index += 1
            line = f"{line}{raw_lines[index]}"

        lines.append(line)
        index += 1
    return lines


def extract_safety_warnings(documents: List[Document]) -> List[str]:
    """从检索到的真实说明书片段抽取安全提示。"""

    warnings: List[str] = []
    seen: set[str] = set()

    for document in documents:
        for line in _logical_lines(document.page_content):
            for sentence in _split_sentences(line):
                normalized = _normalize_text(sentence)
                if not _contains_safety_keyword(normalized):
                    continue
                if len(normalized) < _MIN_WARNING_LENGTH:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                warnings.append(normalized)
                if len(warnings) >= _MAX_WARNINGS:
                    return warnings

    return warnings
