"""政策/说明书文档解析。

解析器只负责把真实文件转成带来源、页码、章节的结构化块；
切块、质量过滤和向量化分别由 chunker / document_quality / document_processor 负责。

支持范围：``.md``、``.txt``、``.pdf``。Word 文档不在本阶段支持范围内。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

import openai
import pymupdf
import yaml
from openai import OpenAI

from core.logger import logger
from rag.document_quality import (
    PageQuality,
    assess_page_quality,
    with_indexing_decision,
)
from settings import get_settings

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "knowledge_base"

SUPPORTED_TEXT_SUFFIXES = {".txt", ".md"}
SUPPORTED_PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = SUPPORTED_TEXT_SUFFIXES | SUPPORTED_PDF_SUFFIXES

SourceType = Literal["policy", "manual"]
BlockType = Literal["text", "table", "image_caption"]
Scalar = str | int | float | bool

# front matter → 检索元数据 的字段映射。
# 只映射政策文档里真实存在的字段，不补任何业务默认值。
POLICY_FRONT_MATTER_MAPPING = {
    "doc_id": "document_id",
    "doc_type": "doc_type",
    "policy_type": "policy_type",
    "region": "region",
    "product_line": "product_line",
    "effective_date": "effective_date",
    "expiration_date": "expiration_date",
    "status": "status",
    "source_url": "url",
}
MANUAL_FRONT_MATTER_MAPPING = {
    "doc_id": "document_id",
    "product_model": "product_model",
    "product_version": "product_version",
    "issue_type": "issue_type",
    "source_url": "url",
}

CAPTION_PROMPT = """这是产品说明书中的示意图。请描述：
1. 图的主题（什么产品/什么场景）
2. 所有标注文字（部件名称），逐个列出
3. 各部件的位置关系（上下、内外、左右）
输出简洁的中文描述，用于检索索引，不要客套话。"""


@dataclass(frozen=True)
class ParsedBlock:
    """解析后的文本块。"""

    text: str
    page: int | None = None
    section: str | None = None
    block_type: BlockType = "text"
    metadata: Mapping[str, Scalar] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    """一个源文件的完整解析结果。"""

    source_path: str
    file_name: str
    source_type: SourceType
    blocks: tuple[ParsedBlock, ...]
    page_qualities: tuple[PageQuality, ...] = ()
    doc_metadata: Mapping[str, Any] = field(default_factory=dict)




def read_text_file(file_path: str | Path) -> str:
    """读取文本文件，UTF-8 失败时回退 GBK。"""
    path = Path(file_path)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        logger.warning("UTF-8 解码失败, 回退 GBK", file=str(path))
        return path.read_text(encoding="gbk")


def _strip_trailing_slash_comment(line: str, file_path: str, line_number: int) -> str:
    """去除 YAML 行尾的 ``//`` 注释。

    只处理同时满足以下条件的 ``//``：
    - 不在单/双引号内；
    - 前面有空白；
    - 不是 URL 中的 ``://``。

    真实业务文档里出现了 ``status: "active"  // ...`` 这种写法，而 PyYAML
    会把 ``//`` 当成正文；这里只做最小容错，YAML 结构错误仍然抛 ValueError。
    """
    in_single_quote = False
    in_double_quote = False

    for index, char in enumerate(line[:-1]):
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
        if char != "/" or line[index + 1] != "/":
            continue
        if in_single_quote or in_double_quote:
            continue
        if index == 0 or not line[index - 1].isspace():
            continue
        if index >= 2 and line[index - 2] == ":":
            continue

        logger.warning(
            "忽略 YAML front matter 行尾 // 注释",
            file=file_path,
            line=line_number,
        )
        return line[:index].rstrip()

    return line


def read_markdown(file_path: str | Path) -> tuple[dict[str, Any], str]:
    """读取 Markdown，并解析文件头部 YAML front matter。"""
    path = Path(file_path)
    content = read_text_file(path)
    if content.startswith("\ufeff"):
        content = content.lstrip("\ufeff")
    lines = content.splitlines(keepends=True)

    if not lines or lines[0].strip() != "---":
        return {}, content

    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        raise ValueError(f"YAML front matter 未闭合: {path}")

    front_matter_lines = [
        _strip_trailing_slash_comment(line, str(path), line_number)
        for line_number, line in enumerate(lines[1:closing_index], start=2)
    ]
    front_matter_text = "".join(front_matter_lines)

    try:
        parsed = yaml.safe_load(front_matter_text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML front matter 解析失败: {path}") from e

    if not isinstance(parsed, dict):
        raise ValueError(f"YAML front matter 必须是键值结构: {path}")

    metadata = {str(key): value for key, value in parsed.items()}
    return metadata, "".join(lines[closing_index + 1:])


def _date_value_to_number(value: Any) -> int | None:
    """把真实生效日期转换成 YYYYMMDD 整数；无法解析时不补默认值。"""
    if isinstance(value, datetime):
        return int(value.date().strftime("%Y%m%d"))
    if isinstance(value, date):
        return int(value.strftime("%Y%m%d"))
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value).date().strftime("%Y%m%d"))
        except ValueError:
            return None
    return None


def map_front_matter(
    front_matter: Mapping[str, Any], mapping: Mapping[str, str]
) -> dict[str, Scalar]:
    """把 front matter 中真实存在的标量字段映射成检索元数据。"""
    metadata: dict[str, Scalar] = {}

    for source_key, target_key in mapping.items():
        value = front_matter.get(source_key)
        if value is None or value == "":
            continue
        if isinstance(value, (dict, list, tuple, set)):
            raise ValueError(f"front matter 字段 {source_key} 必须是标量")
        if isinstance(value, (str, int, float, bool)):
            metadata[target_key] = value
        elif hasattr(value, "isoformat"):
            metadata[target_key] = value.isoformat()
        else:
            raise ValueError(f"front matter 字段 {source_key} 不是可写入元数据的类型")

    return metadata


def _heading_section(heading_stack: list[tuple[int, str]]) -> str | None:
    if not heading_stack:
        return None
    return "/".join(title for _, title in heading_stack)


def _parse_markdown_blocks(body: str) -> tuple[ParsedBlock, ...]:
    """按 Markdown 标题拆章节，并保留表格块。"""
    blocks: list[ParsedBlock] = []
    heading_stack: list[tuple[int, str]] = []
    paragraph_lines: list[str] = []
    table_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        text = "\n".join(paragraph_lines).strip()
        if text:
            blocks.append(
                ParsedBlock(text=text, section=_heading_section(heading_stack), block_type="text")
            )
        paragraph_lines = []

    def flush_table() -> None:
        nonlocal table_lines
        text = "\n".join(table_lines).strip()
        if text:
            blocks.append(
                ParsedBlock(text=text, section=_heading_section(heading_stack), block_type="table")
            )
        table_lines = []

    for line in body.splitlines():
        heading_match = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if heading_match:
            flush_paragraph()
            flush_table()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_stack = [
                (old_level, old_title)
                for old_level, old_title in heading_stack
                if old_level < level
            ]
            heading_stack.append((level, title))
            continue

        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue

        # Markdown 表格行：至少包含一个竖线；已在表格中时继续收集后续行。
        if stripped.startswith("|") or (table_lines and "|" in stripped):
            flush_paragraph()
            table_lines.append(line)
        else:
            flush_table()
            paragraph_lines.append(line)

    flush_paragraph()
    flush_table()
    return tuple(blocks)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("|", "\\|").strip()


def _table_to_markdown(table: Any) -> str:
    """把 PyMuPDF 表格转成 Markdown，保留行列结构。"""
    try:
        rows = table.extract() or []
    except (RuntimeError, ValueError) as e:
        logger.warning("PDF 表格提取失败", table=str(getattr(table, "bbox", "")), error=str(e))
        return ""

    cleaned_rows = [[_cell_text(cell) for cell in row] for row in rows]
    cleaned_rows = [row for row in cleaned_rows if any(cell for cell in row)]
    if not cleaned_rows:
        return ""

    column_count = max(len(row) for row in cleaned_rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in cleaned_rows]
    lines = ["| " + " | ".join(row) + " |" for row in normalized_rows]
    separator = "| " + " | ".join("---" for _ in range(column_count)) + " |"
    lines.insert(1, separator)
    return "\n".join(lines)


def _rect_overlaps(first: Iterable[float], second: Iterable[float]) -> bool:
    first_values = list(first)
    second_values = list(second)
    return not (
        first_values[2] <= second_values[0]
        or first_values[0] >= second_values[2]
        or first_values[3] <= second_values[1]
        or first_values[1] >= second_values[3]
    )



class DocumentParser:
    """政策/说明书解析器。

    OCR 与图片 caption 默认关闭，避免在未确认数据质量和模型能力时改变索引内容。
    """

    def __init__(
        self,
        *,
        ocr_fallback: bool = False,
        caption_images: bool = False,
        vision_model: str | None = None,
        report_dir: str | Path = DEFAULT_REPORT_DIR,
    ) -> None:
        self.ocr_fallback = ocr_fallback
        self.caption_images = caption_images
        self.vision_model = vision_model
        self.report_dir = Path(report_dir)
        self.caption_cache_path = self.report_dir / "caption_cache.json"
        self.image_dir = self.report_dir / "images"

        self._ocr_engine: Any | None = None
        self._vision_client: OpenAI | None = None
        self._caption_cache: dict[str, str] | None = None

        if caption_images and not self.vision_model:
            settings = get_settings()
            self.vision_model = getattr(settings, "VISION_MODEL", None)
        if caption_images and not self.vision_model:
            raise ValueError(
                "启用 --caption-images 需要配置 VISION_MODEL，"
                "或显式传入支持图片输入的视觉模型"
            )

    # --------------------------------------------------------------- 公开入口
    def parse_document(self, file_path: str | Path, source_type: SourceType) -> ParsedDocument:
        """解析单个文件。"""
        path = Path(file_path)
        if source_type == "policy":
            return self.parse_policy_document(path)
        if source_type == "manual":
            return self.parse_manual_document(path)
        raise ValueError(f"未知文档类型: {source_type}")

    def parse_policy_document(self, file_path: str | Path) -> ParsedDocument:
        return self._parse_document(file_path, source_type="policy")

    def parse_manual_document(self, file_path: str | Path) -> ParsedDocument:
        return self._parse_document(file_path, source_type="manual")

    # --------------------------------------------------------------- 分发处理
    def _parse_document(self, file_path: str | Path, source_type: SourceType) -> ParsedDocument:
        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError(f"不支持的文档格式: {path.suffix} ({path})")

        mapping = (
            POLICY_FRONT_MATTER_MAPPING
            if source_type == "policy"
            else MANUAL_FRONT_MATTER_MAPPING
        )
        common_metadata: dict[str, Any] = {
            "source_type": source_type,
            "file_name": path.name,
            "file_path": str(path),
            "source": path.stem,
        }

        if suffix in SUPPORTED_TEXT_SUFFIXES:
            if suffix == ".md":
                front_matter, body = read_markdown(path)
                mapped = map_front_matter(front_matter, mapping)
            else:
                body = read_text_file(path)
                mapped = {}

            document_id = str(mapped.get("document_id") or path.stem)
            doc_metadata = {
                **common_metadata,
                **mapped,
                "document_id": document_id,
            }
            # Chroma 1.x 的区间过滤只接受数字；保留原 effective_date 字符串
            # 用于展示，同时从真实值派生可过滤的 YYYYMMDD 整数。
            effective_date_num = _date_value_to_number(mapped.get("effective_date"))
            if effective_date_num is not None:
                doc_metadata["effective_date_num"] = effective_date_num
            if suffix == ".md":
                blocks = _parse_markdown_blocks(body)
            else:
                text = body.strip()
                blocks = (ParsedBlock(text=text, section=None, block_type="text"),) if text else ()

            if not blocks:
                raise ValueError(f"文档没有可解析内容: {path}")

            return ParsedDocument(
                source_path=str(path),
                file_name=path.name,
                source_type=source_type,
                blocks=blocks,
                doc_metadata=doc_metadata,
            )

        return self._parse_pdf(path, source_type=source_type, common_metadata=common_metadata)


    # ------------------------------------------------------------------- PDF
    def _parse_pdf(
        self,
        path: Path,
        *,
        source_type: SourceType,
        common_metadata: Mapping[str, Any],
    ) -> ParsedDocument:
        try:
            document = pymupdf.open(str(path))
        except (pymupdf.FileDataError, RuntimeError) as e:
            raise ValueError(f"PDF 解析失败: {path}") from e

        try:
            if document.needs_pass:
                raise ValueError(f"PDF 已加密, 无法解析: {path}")
            if document.page_count == 0:
                raise ValueError(f"PDF 没有页面: {path}")

            pdf_metadata = document.metadata or {}
            pdf_title = str(pdf_metadata.get("title") or "").strip()
            document_id = str(common_metadata.get("document_id") or path.stem)
            doc_metadata = {
                **common_metadata,
                "document_id": document_id,
            }
            if pdf_title:
                doc_metadata["pdf_title"] = pdf_title

            blocks: list[ParsedBlock] = []
            page_qualities: list[PageQuality] = []

            for page_number, page in enumerate(document, start=1):
                try:
                    page_text = page.get_text("text") or ""
                    tables = self._find_tables(page)
                    has_images = bool(page.get_images(full=True))
                except (RuntimeError, ValueError) as e:
                    raise ValueError(
                        f"PDF 第 {page_number} 页文本提取失败: {path}"
                    ) from e

                native_quality = assess_page_quality(
                    page_text,
                    page_number,
                    source="native",
                    has_images=has_images,
                )
                if self.ocr_fallback and self._needs_ocr(native_quality):
                    page_text, confidence = self._ocr_page(page, page_number, path)
                    quality = assess_page_quality(
                        page_text,
                        page_number,
                        source="ocr",
                        ocr_confidence=confidence,
                        has_images=has_images,
                    )
                else:
                    quality = native_quality

                caption_blocks = self._build_caption_blocks(document, page, page_number, path)
                quality = with_indexing_decision(
                    quality,
                    has_table=bool(tables),
                    has_caption=bool(caption_blocks),
                )
                page_qualities.append(quality)

                if not quality.indexed:
                    logger.info(
                        "跳过低质量 PDF 页面",
                        file=str(path),
                        page=page_number,
                        issues=quality.issues,
                        reason=quality.skip_reason,
                    )
                    continue

                page_blocks = self._extract_page_blocks(page, page_text, tables, page_number)
                if not page_blocks and not caption_blocks:
                    logger.warning("PDF 页面没有可索引内容", file=str(path), page=page_number)
                    continue
                blocks.extend(page_blocks)
                blocks.extend(caption_blocks)

            return ParsedDocument(
                source_path=str(path),
                file_name=path.name,
                source_type=source_type,
                blocks=tuple(blocks),
                page_qualities=tuple(page_qualities),
                doc_metadata=doc_metadata,
            )
        finally:
            document.close()

    def _find_tables(self, page: Any) -> list[Any]:
        try:
            return list(page.find_tables().tables)
        except (RuntimeError, ValueError) as e:
            logger.warning("PDF 表格检测失败", page=str(getattr(page, "number", "")), error=str(e))
            return []


    def _extract_page_blocks(
        self,
        page: Any,
        page_text: str,
        tables: list[Any],
        page_number: int,
    ) -> list[ParsedBlock]:
        """提取页面块，表格区域只保留结构化表格文本，避免与正文重复。"""
        if not tables:
            text = page_text.strip()
            return [ParsedBlock(text=text, page=page_number, block_type="text")] if text else []

        table_texts: list[tuple[float, float, ParsedBlock]] = []
        table_rects: list[list[float]] = []
        for table_index, table in enumerate(tables):
            table_text = _table_to_markdown(table)
            if not table_text:
                continue
            bbox = list(table.bbox)
            table_rects.append(bbox)
            table_texts.append(
                (
                    float(bbox[1]),
                    float(bbox[0]),
                    ParsedBlock(
                        text=table_text,
                        page=page_number,
                        block_type="table",
                        metadata={"table_index": table_index},
                    ),
                )
            )

        text_blocks: list[tuple[float, float, ParsedBlock]] = []
        try:
            raw_blocks = page.get_text("blocks")
        except RuntimeError as e:
            raise ValueError(f"PDF 页面文本块提取失败, 第 {page_number} 页") from e

        for raw_block in raw_blocks:
            # PyMuPDF block: (x0, y0, x1, y1, text, block_no, block_type)
            if len(raw_block) < 7 or raw_block[6] != 0:
                continue
            block_bbox = list(raw_block[:4])
            if any(_rect_overlaps(block_bbox, table_rect) for table_rect in table_rects):
                continue
            text = str(raw_block[4]).strip()
            if not text:
                continue
            text_blocks.append(
                (
                    float(block_bbox[1]),
                    float(block_bbox[0]),
                    ParsedBlock(text=text, page=page_number, block_type="text"),
                )
            )

        ordered = sorted(text_blocks + table_texts, key=lambda item: (item[0], item[1]))
        blocks = [item[2] for item in ordered]

        # 表格检测存在边界情况：若没有识别出表格且正文块为空，回退整页文本。
        if not blocks and page_text.strip():
            blocks = [ParsedBlock(text=page_text.strip(), page=page_number, block_type="text")]
        return blocks

    def _needs_ocr(self, quality: PageQuality) -> bool:
        recoverable_issues = {
            "blank",
            "page_num_only",
            "garbled",
            "too_short",
            "image_only",
        }
        return bool(set(quality.issues) & recoverable_issues)


    def _ocr_page(self, page: Any, page_number: int, path: Path) -> tuple[str, float | None]:
        """对单页执行 OCR，返回文本与平均置信度。"""
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        try:
            from paddleocr import PaddleOCR
        except ImportError as e:
            raise ValueError("启用 OCR 需要安装 paddleocr") from e

        if self._ocr_engine is None:
            self._ocr_engine = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )

        try:
            pixmap = page.get_pixmap(dpi=200, alpha=False)
            image = self._pixmap_to_numpy(pixmap)
            results = self._ocr_engine.predict(image)
        except (RuntimeError, ValueError) as e:
            raise ValueError(f"OCR 失败: {path} 第 {page_number} 页") from e

        texts: list[str] = []
        scores: list[float] = []
        for result in results:
            payload = dict(result)
            texts.extend(str(text) for text in payload.get("rec_texts", []) if str(text).strip())
            scores.extend(float(score) for score in payload.get("rec_scores", []))

        text = "\n".join(texts).strip()
        confidence = sum(scores) / len(scores) if scores else None
        return text, confidence

    @staticmethod
    def _pixmap_to_numpy(pixmap: Any) -> Any:
        import numpy as np

        image = np.frombuffer(pixmap.samples, dtype=np.uint8)
        if pixmap.n == 4:
            image = image.reshape(pixmap.height, pixmap.width, 4)[:, :, :3]
        else:
            image = image.reshape(pixmap.height, pixmap.width, pixmap.n)
        return image

    # ------------------------------------------------------------ 图片 caption
    def _build_caption_blocks(
        self, document: Any, page: Any, page_number: int, path: Path
    ) -> list[ParsedBlock]:
        if not self.caption_images:
            return []

        blocks: list[ParsedBlock] = []
        for image_info in page.get_images(full=True):
            xref = image_info[0]
            try:
                image = document.extract_image(xref)
            except (RuntimeError, ValueError) as e:
                raise ValueError(f"PDF 图片提取失败: {path} 第 {page_number} 页") from e

            image_bytes = image.get("image")
            if not image_bytes:
                continue
            image_hash = hashlib.md5(image_bytes).hexdigest()
            caption = self._get_or_create_caption(image_bytes, image_hash, image.get("ext", "png"))
            image_path = (
                self.image_dir
                / f"{path.stem}_p{page_number}_{xref}.{image.get('ext', 'png')}"
            )
            image_path.parent.mkdir(parents=True, exist_ok=True)
            if not image_path.exists():
                image_path.write_bytes(image_bytes)
            blocks.append(
                ParsedBlock(
                    text=caption,
                    page=page_number,
                    block_type="image_caption",
                    metadata={"image_path": str(image_path), "image_hash": image_hash},
                )
            )
        return blocks

    def _get_or_create_caption(self, image_bytes: bytes, image_hash: str, extension: str) -> str:
        if self._caption_cache is None:
            if self.caption_cache_path.exists():
                try:
                    cache = json.loads(
                        self.caption_cache_path.read_text(encoding="utf-8")
                    )
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"caption_cache 不是合法 JSON: {self.caption_cache_path}"
                    ) from e
                if not isinstance(cache, dict):
                    raise ValueError(f"caption_cache 必须是键值结构: {self.caption_cache_path}")
                self._caption_cache = cache
            else:
                self._caption_cache = {}

        if image_hash in self._caption_cache:
            return self._caption_cache[image_hash]

        if self._vision_client is None:
            settings = get_settings()
            self._vision_client = OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIME_OUT,
            )

        encoded = base64.b64encode(image_bytes).decode("ascii")
        mime_type = "image/png" if extension.lower() == "png" else f"image/{extension}"
        try:
            response = self._vision_client.chat.completions.create(
                model=self.vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": CAPTION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                            },
                        ],
                    }
                ],
                temperature=0.1,
            )
        except (openai.APIError, ValueError) as e:
            raise ValueError("视觉模型生成图片 caption 失败") from e

        if not response.choices:
            raise ValueError("视觉模型未返回任何候选结果")
        caption = response.choices[0].message.content
        if not caption or not caption.strip():
            raise ValueError("视觉模型未返回图片 caption")

        self._caption_cache[image_hash] = caption.strip()
        self.caption_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.caption_cache_path.write_text(
            json.dumps(self._caption_cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return caption.strip()
