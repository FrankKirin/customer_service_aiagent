"""文档向量化处理器。

职责：
- 发现 raw_data 下的真实文档；
- 调用 DocumentParser 得到结构化块；
- 调用 chunk_by_sections 切块，并补齐稳定 ID / 来源元数据；
- 通过 VectorStoreManager 的 upsert 接口写入 ChromaDB。

约定（见 .clinerules）：
- 不使用模拟数据兜底：目录为空/格式不支持时明确报错；
- 元数据只来自真实文件（YAML front matter / PDF 自带信息）或解析结构；
- 异常精确捕获，不用裸 Exception；
- 异步方法使用原生异步 embedding，阻塞解析放到线程池。

运行方式（项目根目录）：
    python -m rag.document_processor --reset
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional

import anyio.to_thread
import chromadb
import openai
from langchain_core.documents import Document

from core.logger import logger
from rag.chunker import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_MIN_CHARS,
    chunk_by_sections,
)
from rag.document_parser import (
    DEFAULT_REPORT_DIR,
    MANUAL_FRONT_MATTER_MAPPING,
    POLICY_FRONT_MATTER_MAPPING,
    SUPPORTED_PDF_SUFFIXES,
    SUPPORTED_SUFFIXES,
    SUPPORTED_TEXT_SUFFIXES,
    DocumentParser,
    ParsedDocument,
    map_front_matter,
    read_markdown,
    read_text_file,
)
from rag.vector_store import get_vector_store_manager

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_POLICY_DIR = str(PROJECT_ROOT / "raw_data" / "policy_docs")
DEFAULT_MANUAL_DIR = str(PROJECT_ROOT / "raw_data" / "manual_docs")

# 兼容旧调用方的常量名
CHUNK_SIZE = DEFAULT_CHUNK_SIZE
CHUNK_OVERLAP = DEFAULT_CHUNK_OVERLAP


class DocumentProcessor:
    """文档向量化处理器；解析阶段不初始化 ChromaDB / embedding。"""

    def __init__(
        self,
        *,
        ocr_fallback: bool = False,
        caption_images: bool = False,
        vision_model: str | None = None,
        chunk_size: int = CHUNK_SIZE,
        overlap: int = CHUNK_OVERLAP,
        min_chars: int = DEFAULT_MIN_CHARS,
        report_dir: str | Path = DEFAULT_REPORT_DIR,
        parser: DocumentParser | None = None,
    ) -> None:
        self._vector_store: Optional[Any] = None
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chars = min_chars
        self.parser = parser or DocumentParser(
            ocr_fallback=ocr_fallback,
            caption_images=caption_images,
            vision_model=vision_model,
            report_dir=report_dir,
        )

    @property
    def vector_store(self) -> Any:
        """向量存储管理器（首次写入/检索时才初始化）。"""
        if self._vector_store is None:
            self._vector_store = get_vector_store_manager()
        return self._vector_store

    async def _aget_vector_store(self) -> Any:
        """异步路径下把可能阻塞的向量库初始化放到线程池。"""
        if self._vector_store is None:
            self._vector_store = await anyio.to_thread.run_sync(get_vector_store_manager)
        return self._vector_store

    # ------------------------------------------------------------- 文件发现
    @staticmethod
    def discover_files(directory: str | Path) -> List[str]:
        """列出一个目录下支持解析的文档文件。"""
        path = Path(directory)
        if not path.is_dir():
            raise FileNotFoundError(f"文档目录不存在: {directory}")

        files = sorted(
            str(item)
            for item in path.iterdir()
            if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
        )
        logger.info("扫描文档目录", directory=str(directory), files=len(files))
        return files

    # --------------------------------------------------------- 兼容旧解析接口
    @staticmethod
    def _read_text_file(file_path: str) -> str:
        return read_text_file(file_path)

    @staticmethod
    def _read_markdown(file_path: str):
        return read_markdown(file_path)

    @staticmethod
    def _map_front_matter(front_matter, mapping):
        return map_front_matter(front_matter, mapping)

    # --------------------------------------------------------------- 构建文档
    def build_policy_documents(
        self, file_path: str | Path
    ) -> tuple[List[Document], ParsedDocument]:
        parsed = self.parser.parse_policy_document(file_path)
        return self._to_documents(parsed), parsed

    def build_manual_documents(
        self, file_path: str | Path
    ) -> tuple[List[Document], ParsedDocument]:
        parsed = self.parser.parse_manual_document(file_path)
        return self._to_documents(parsed), parsed

    def _build_policy_documents(self, file_path: str | Path) -> List[Document]:
        documents, _ = self.build_policy_documents(file_path)
        return documents

    def _build_manual_documents(self, file_path: str | Path) -> List[Document]:
        documents, _ = self.build_manual_documents(file_path)
        return documents


    # ----------------------------------------------------------- 元数据与切块
    def _to_documents(self, parsed: ParsedDocument) -> List[Document]:
        base_metadata = self._base_metadata(parsed)
        chunks = chunk_by_sections(
            parsed.blocks,
            max_chars=self.chunk_size,
            overlap=self.overlap,
            min_chars=self.min_chars,
        )

        documents: List[Document] = []
        seen_contents: set[str] = set()
        for chunk in chunks:
            # 同一个 document 内完全相同的块只保留第一次出现，避免表格跨页重复行
            # 或重叠切分造成重复向量；chunk_index 在去重后的最终序列上重新编号。
            normalized_content = chunk.content.strip()
            if normalized_content in seen_contents:
                continue
            seen_contents.add(normalized_content)

            output_index = len(documents)
            metadata = dict(base_metadata)
            metadata.update(chunk.metadata)

            metadata["chunk_index"] = output_index
            metadata["block_type"] = chunk.block_type
            if chunk.section is not None:
                metadata["section"] = chunk.section
            if chunk.page is not None:
                metadata["page"] = chunk.page

            # 稳定 ID：文档 ID + 页码(无页码记 0) + 最终输出块序号。
            document_id = str(base_metadata["document_id"])
            chunk_id = f"{document_id}::{chunk.page or 0}::{output_index}"
            metadata["chunk_id"] = chunk_id
            # 旧代码/检索来源信息读取 doc_id，保留为同一个稳定 ID。
            metadata["doc_id"] = chunk_id

            self._validate_metadata(metadata, parsed.source_path)
            documents.append(Document(page_content=chunk.content, metadata=metadata))

        if not documents:
            raise ValueError(f"文档没有可向量化内容: {parsed.source_path}")
        return documents

    @staticmethod
    def _base_metadata(parsed: ParsedDocument) -> dict[str, Any]:
        metadata: dict[str, Any] = dict(parsed.doc_metadata)
        metadata.setdefault("document_id", Path(parsed.source_path).stem)
        metadata.setdefault("source_type", parsed.source_type)
        metadata.setdefault("file_name", parsed.file_name)
        metadata.setdefault("source", Path(parsed.source_path).stem)
        return metadata

    @staticmethod
    def _validate_metadata(metadata: dict[str, Any], source_path: str) -> None:
        required_keys = {"document_id", "source_type", "file_name", "chunk_id"}
        missing = required_keys - set(metadata)
        if missing:
            raise ValueError(f"文档块缺少必需元数据 {sorted(missing)}: {source_path}")

        for key, value in metadata.items():
            if value is None or value == "":
                raise ValueError(f"文档元数据 {key} 为空: {source_path}")
            if isinstance(value, (str, int, float, bool)):
                continue
            raise ValueError(
                f"文档元数据 {key} 必须是 str/int/float/bool, 当前为 {type(value).__name__}: "
                f"{source_path}"
            )

    @staticmethod
    def _document_ids(documents: Iterable[Document]) -> List[str]:
        return [str(document.metadata["chunk_id"]) for document in documents]

    # --------------------------------------------------------------- 处理流程
    def process_policy_documents(
        self,
        directory: str = DEFAULT_POLICY_DIR,
        reset: bool = False,
    ) -> List[str]:
        """同步处理政策文档目录并写入向量库。"""
        documents, _ = self._parse_directory(directory, source_type="policy")
        if reset:
            self.vector_store.reset_policy_collection()
        ids = self.vector_store.upsert_policy_documents(
            documents, self._document_ids(documents)
        )
        logger.info("政策文档向量化完成", chunks=len(ids))
        return ids

    def process_manual_documents(
        self,
        directory: str = DEFAULT_MANUAL_DIR,
        reset: bool = False,
    ) -> List[str]:
        """同步处理说明书文档目录并写入向量库。"""
        documents, _ = self._parse_directory(directory, source_type="manual")
        if reset:
            self.vector_store.reset_manual_collection()
        ids = self.vector_store.upsert_manual_documents(
            documents, self._document_ids(documents)
        )
        logger.info("说明书文档向量化完成", chunks=len(ids))
        return ids

    async def aprocess_policy_documents(
        self,
        directory: str = DEFAULT_POLICY_DIR,
        reset: bool = False,
    ) -> List[str]:
        """异步处理政策文档目录并写入向量库（真异步）。"""
        documents, _ = await self._aparse_directory(directory, source_type="policy")
        manager = await self._aget_vector_store()
        if reset:
            await anyio.to_thread.run_sync(manager.reset_policy_collection)
        ids = await manager.aupsert_policy_documents(
            documents, self._document_ids(documents)
        )
        logger.info("政策文档向量化完成(异步)", chunks=len(ids))
        return ids

    async def aprocess_manual_documents(
        self,
        directory: str = DEFAULT_MANUAL_DIR,
        reset: bool = False,
    ) -> List[str]:
        """异步处理说明书文档目录并写入向量库（真异步）。"""
        documents, _ = await self._aparse_directory(directory, source_type="manual")
        manager = await self._aget_vector_store()
        if reset:
            await anyio.to_thread.run_sync(manager.reset_manual_collection)
        ids = await manager.aupsert_manual_documents(
            documents, self._document_ids(documents)
        )
        logger.info("说明书文档向量化完成(异步)", chunks=len(ids))
        return ids


    def _parse_directory(
        self,
        directory: str,
        *,
        source_type: str,
    ) -> tuple[List[Document], List[ParsedDocument]]:
        """同步解析目录；任一文件失败则整体不进入写入阶段。"""
        files = self.discover_files(directory)
        if not files:
            raise FileNotFoundError(f"文档目录中没有可解析的文件: {directory}")

        documents: List[Document] = []
        parsed_documents: List[ParsedDocument] = []
        failures: List[str] = []

        for file_path in files:
            logger.info("解析文档", file=file_path, source_type=source_type)
            try:
                if source_type == "policy":
                    file_documents, parsed = self.build_policy_documents(file_path)
                else:
                    file_documents, parsed = self.build_manual_documents(file_path)
                documents.extend(file_documents)
                parsed_documents.append(parsed)
            except (FileNotFoundError, PermissionError, UnicodeDecodeError, ValueError) as e:
                logger.error("解析文档失败", file=file_path, error=str(e))
                failures.append(f"{file_path}: {e}")

        if failures:
            raise ValueError(
                f"{len(failures)} 个{source_type}文件解析失败: {'; '.join(failures)}"
            )
        if not documents:
            raise ValueError(f"{source_type}文档没有可向量化的文本内容: {directory}")

        return documents, parsed_documents

    async def _aparse_directory(
        self,
        directory: str,
        *,
        source_type: str,
    ) -> tuple[List[Document], List[ParsedDocument]]:
        """异步解析目录；文件解析在线程池中执行，embedding 写入由调用方 await。"""
        files = await anyio.to_thread.run_sync(self.discover_files, directory)
        if not files:
            raise FileNotFoundError(f"文档目录中没有可解析的文件: {directory}")

        documents: List[Document] = []
        parsed_documents: List[ParsedDocument] = []
        failures: List[str] = []

        for file_path in files:
            logger.info("解析文档(异步)", file=file_path, source_type=source_type)
            try:
                if source_type == "policy":
                    file_documents, parsed = await anyio.to_thread.run_sync(
                        self.build_policy_documents, file_path
                    )
                else:
                    file_documents, parsed = await anyio.to_thread.run_sync(
                        self.build_manual_documents, file_path
                    )
                documents.extend(file_documents)
                parsed_documents.append(parsed)
            except (FileNotFoundError, PermissionError, UnicodeDecodeError, ValueError) as e:
                logger.error("解析文档失败(异步)", file=file_path, error=str(e))
                failures.append(f"{file_path}: {e}")

        if failures:
            raise ValueError(
                f"{len(failures)} 个{source_type}文件解析失败: {'; '.join(failures)}"
            )
        if not documents:
            raise ValueError(f"{source_type}文档没有可向量化的文本内容: {directory}")

        return documents, parsed_documents


# CLI 统一收敛的异常类型（目录/文件、编码、格式、向量库、embedding 接口）
INGEST_ERRORS = (
    FileNotFoundError,
    PermissionError,
    UnicodeDecodeError,
    ValueError,
    chromadb.errors.ChromaError,
    openai.APIError,
)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 raw_data 下的政策/说明书文档向量化到 ChromaDB")
    parser.add_argument("--policy-dir", default=DEFAULT_POLICY_DIR, help="政策文档目录")
    parser.add_argument("--manual-dir", default=DEFAULT_MANUAL_DIR, help="说明书文档目录")
    parser.add_argument("--reset", action="store_true", help="先清空已有集合再向量化, 避免重复数据")
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE, help="切块最大字符数")
    parser.add_argument("--overlap", type=int, default=CHUNK_OVERLAP, help="正文块重叠字符数")
    parser.add_argument("--ocr-fallback", action="store_true", help="对低质量 PDF 页面启用 OCR")
    parser.add_argument("--caption-images", action="store_true", help="为 PDF 图片生成检索 caption")
    parser.add_argument("--vision-model", default=None, help="图片 caption 使用的视觉模型")
    return parser.parse_args(argv)


async def _ingest(args: argparse.Namespace) -> int:
    """执行向量化流程，返回进程退出码。"""
    processor = DocumentProcessor(
        ocr_fallback=args.ocr_fallback,
        caption_images=args.caption_images,
        vision_model=args.vision_model,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )
    exit_code = 0

    print("=== 开始文档向量化处理 ===")

    print("1. 处理政策文档 ...")
    try:
        policy_ids = await processor.aprocess_policy_documents(args.policy_dir, reset=args.reset)
        print(f"   政策文档块入库: {len(policy_ids)}")
    except INGEST_ERRORS as e:
        logger.error("政策文档向量化失败", error=str(e))
        print(f"   [失败] 政策文档: {e}")
        exit_code = 1

    print("2. 处理说明书文档 ...")
    try:
        manual_ids = await processor.aprocess_manual_documents(args.manual_dir, reset=args.reset)
        print(f"   说明书文档块入库: {len(manual_ids)}")
    except INGEST_ERRORS as e:
        logger.error("说明书文档向量化失败", error=str(e))
        print(f"   [失败] 说明书文档: {e}")
        exit_code = 1

    print("3. 当前集合数量")
    manager = await processor._aget_vector_store()
    for collection_name in ("policy_docs", "manual_docs"):
        try:
            count = await anyio.to_thread.run_sync(
                manager.get_collection_count, collection_name
            )
            print(f"   {collection_name}: {count}")
        except (chromadb.errors.ChromaError, ValueError) as e:
            logger.error("读取集合数量失败", collection=collection_name, error=str(e))
            exit_code = 1

    print("=== 处理结束 ===")
    return exit_code


def main() -> None:
    args = _parse_args()
    try:
        exit_code = asyncio.run(_ingest(args))
    except KeyboardInterrupt:
        print("已中断")
        sys.exit(130)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
