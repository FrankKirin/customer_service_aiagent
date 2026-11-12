"""PDF 页面质量检测。

这里只根据页面真实文本、OCR 置信度和页面结构做判断，不猜测或补写业务内容。
判定规则迁移自 raw_data/manual_docs/pdf_quanlity_check.py，并修复了目录页省略号
被当成异常字符、以及“标题 + 页码”残片无法识别的问题。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

QualitySource = Literal["native", "ocr", "image_caption"]

# 页码可能单独占一行，也可能出现在标题后面。统一按“整行”删除，避免把正文数字误删。
PAGE_NUMBER_PATTERNS = (
    r"-?\s*\d{1,4}\s*-?",
    r"第\s*\d+\s*页",
    r"\d+\s*/\s*\d+",
    r"page\s+\d+",
)

# 常见中英文标点与排版符号。省略号、间隔号是目录/版面中真实存在的符号，不应算乱码。
ALLOWED_PUNCTUATION = set(
    "，。、；：？！“”‘’（）《》〈〉【】「」『』…·—–－-"
    ",.;:?!()[]{}<>/\\|_=+*%￥$#@&~`^"
    "℃°±×÷≤≥Ω①②③④⑤⑥⑦⑧⑨⑩•▪◆※√→←↑↓"
)


@dataclass(frozen=True)
class PageQuality:
    """单页质量信息。

    source 表示最终采用哪一层文本；indexed 表示最终是否进入索引；
    skip_reason 只在 indexed=False 时有值，便于构建报告解释“为什么跳过”。
    """

    page: int
    text_length: int
    source: QualitySource = "native"
    ocr_confidence: float | None = None
    issues: tuple[str, ...] = ()
    indexed: bool = True
    skip_reason: str | None = None


def effective_length(text: str) -> int:
    """返回去掉空白后的有效字符数。"""
    return len(re.sub(r"\s", "", text or ""))


def _strip_page_number_lines(text: str) -> str:
    """删除所有“纯页码行”，保留正文行。"""
    kept_lines: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and any(
            re.fullmatch(pattern, stripped, flags=re.IGNORECASE)
            for pattern in PAGE_NUMBER_PATTERNS
        ):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)


def is_page_num_only(text: str) -> bool:
    """判断页面是否只剩页码，或只剩“标题 + 页码”这类无检索价值的残片。"""
    if not (text or "").strip():
        return False
    return effective_length(_strip_page_number_lines(text)) < 10


def _is_allowed_char(char: str) -> bool:
    if char.isalnum() or char.isspace():
        return True
    if "\u4e00" <= char <= "\u9fff":
        return True
    return char in ALLOWED_PUNCTUATION


def is_garbled(text: str) -> bool:
    """判断文本是否乱码。

    替换符/``(cid:N)`` 属于明确的解码失败；其他字符按“异常字符占比”判定。
    目录中的 ``……``、间隔号等合法排版符号已列入白名单，不参与异常字符统计。
    """
    content = text or ""
    total = len(content)
    if total == 0:
        return False

    bad_count = content.count("\ufffd") + len(re.findall(r"\(cid:\d+\)", content))
    weird_count = sum(1 for char in content if not _is_allowed_char(char))
    return bad_count / total > 0.10 or weird_count / total > 0.30


def assess_page_quality(
    text: str,
    page: int,
    *,
    source: QualitySource = "native",
    ocr_confidence: float | None = None,
    has_images: bool = False,
) -> PageQuality:
    """根据一层文本生成页面质量信息，不决定是否入库。"""
    text = text or ""
    length = effective_length(text)
    issues: list[str] = []

    # 先识别“标题 + 页码/纯页码”残片：即使有效字符少于 5 个，也应归为
    # page_num_only，而不是笼统的 blank，构建报告才能解释具体原因。
    if is_page_num_only(text):
        issues.append("page_num_only")
    elif length < 5:
        # 无文本但有图片时标记 image_only，交给 caption 决定是否可索引。
        issues.append("image_only" if has_images else "blank")
    elif is_garbled(text):
        issues.append("garbled")
    elif length < 30:
        issues.append("too_short")

    if source == "ocr" and ocr_confidence is not None:
        if ocr_confidence < 0.50:
            issues.append("low_ocr_confidence")
            issues.append("needs_review")
        elif ocr_confidence < 0.60:
            issues.append("suspect_ocr")
            issues.append("needs_review")

    return PageQuality(
        page=page,
        text_length=length,
        source=source,
        ocr_confidence=ocr_confidence,
        issues=tuple(issues),
    )


def decide_page_indexing(
    quality: PageQuality,
    *,
    has_table: bool = False,
    has_caption: bool = False,
) -> tuple[bool, str | None]:
    """决定这一页是否进入索引，并返回可写入报告的跳过原因。"""
    issues = set(quality.issues)

    if "blank" in issues:
        return False, "空白页，无有效文本"
    if "page_num_only" in issues:
        return False, "仅页码或标题+页码残片，无有效正文"
    if "garbled" in issues:
        return False, "文本乱码，未通过质量检测"
    if "low_ocr_confidence" in issues:
        return False, "OCR 置信度过低，需人工复核"
    if "suspect_ocr" in issues:
        return False, "OCR 置信度偏低，默认不入库"

    if "too_short" in issues or "image_only" in issues:
        if has_table or has_caption:
            return True, None
        return False, "有效文本过短且没有表格/图片 caption"

    return True, None


def with_indexing_decision(
    quality: PageQuality,
    *,
    has_table: bool = False,
    has_caption: bool = False,
) -> PageQuality:
    """返回带有最终入库决策的 PageQuality。"""
    indexed, skip_reason = decide_page_indexing(
        quality, has_table=has_table, has_caption=has_caption
    )
    return PageQuality(
        page=quality.page,
        text_length=quality.text_length,
        source=quality.source,
        ocr_confidence=quality.ocr_confidence,
        issues=quality.issues,
        indexed=indexed,
        skip_reason=skip_reason,
    )
