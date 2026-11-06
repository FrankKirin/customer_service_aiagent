"""
文档向量化处理器
读取 raw_data 下的政策文档和说明书文档, 切块后写入 ChromaDB

约定（见 .clinerules）:
- 不做任何模拟数据兜底: 目录为空/格式不支持时明确报错, 由使用者补齐真实文档
- 文档元数据全部来自真实文件(YAML front matter / PDF 自带信息), 不硬编码任何业务默认值
- 异常精确捕获(FileNotFoundError / PermissionError / UnicodeDecodeError / ValueError / ChromaError / APIError)
- 提供真异步实现: 向量化写入 await 底层异步接口; 阻塞的文件读取放在线程池中执行, 不阻塞事件循环

运行方式(必须在项目根目录用模块方式运行, 否则 core/rag 包无法导入):
    python -m rag.document_processor --reset
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import anyio.to_thread
import chromadb
import openai
import yaml
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from core.logger import logger
from rag.vector_store import get_vector_store_manager

PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_POLICY_DIR = str(PROJECT_ROOT / "raw_data" / "policy_docs")
DEFAULT_MANUAL_DIR = str(PROJECT_ROOT / "raw_data" / "manual_docs")

SUPPORTED_TEXT_SUFFIXES = {".txt", ".md"}
SUPPORTED_PDF_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = SUPPORTED_TEXT_SUFFIXES | SUPPORTED_PDF_SUFFIXES

# 切块参数: 500字符约等于一个条款/一个操作步骤的粒度, 50字符重叠保证跨块语义连续
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# front matter → 检索元数据 的字段映射(只映射真实存在的字段)
POLICY_FRONT_MATTER_MAPPING = {
    "doc_id": "document_id",
    "doc_type": "policy_type",
    "region": "region",
    "product_line": "product_line",
    "policy_type": "policy_type",
    "effective_date": "effective_date",
    "source_url": "url",
}
MANUAL_FRONT_MATTER_MAPPING = {
    "doc_id": "document_id",
    "product_model": "product_model",
    "product_version": "product_version",
    "issue_type": "issue_type",
    "source_url": "url",
}


def build_text_splitter() -> RecursiveCharacterTextSplitter:
    """构建中文友好的切块器(优先按段落/句子切分)"""
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    )


class DocumentProcessor:
    """文档向量化处理器"""

    def __init__(self) -> None:
        # 向量库懒加载: 只做解析时可以完全不初始化 ChromaDB 与 embedding
        self._vector_store: Optional[Any] = None
        self.text_splitter = build_text_splitter()

    @property
    def vector_store(self) -> Any:
        """向量存储管理器(首次使用时初始化)"""
        if self._vector_store is None:
            self._vector_store = get_vector_store_manager()
        return self._vector_store

    # ------------------------------------------------------------- 文件发现
    @staticmethod
    def discover_files(directory: str) -> List[str]:
        """列出一个目录下支持解析的文档文件"""
        path = Path(directory)
        if not path.is_dir():
            raise FileNotFoundError(f"文档目录不存在: {directory}")

        files = sorted(
            str(item)
            for item in path.iterdir()
            if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
        )
        logger.info("扫描文档目录", directory=directory, files=len(files))
        return files

    # --------------------------------------------------------------- 文件读取
    @staticmethod
    def _read_text_file(file_path: str) -> str:
        """读取文本文件, UTF-8失败时回退GBK(中文文档常见编码)"""
        path = Path(file_path)
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("UTF-8解码失败, 回退GBK", file=file_path)
            return path.read_text(encoding="gbk")

    @classmethod
    def _read_markdown(cls, file_path: str) -> Tuple[Dict[str, Any], str]:
        """读取markdown, 并把文件头部的YAML front matter解析成元数据"""
        content = cls._read_text_file(file_path)
        lines = content.splitlines(keepends=True)

        if not lines or lines[0].strip() != "---":
            return {}, content

        for index in range(1, len(lines)):
            if lines[index].strip() != "---":
                continue
            front_matter_text = "".join(lines[1:index])
            try:
                parsed = yaml.safe_load(front_matter_text) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"YAML front matter 解析失败: {file_path}") from e
            if not isinstance(parsed, dict):
                raise ValueError(f"YAML front matter 必须是键值结构: {file_path}")
            metadata = {str(key): value for key, value in parsed.items()}
            return metadata, "".join(lines[index + 1:])

        return {}, content

    @staticmethod
    def _read_pdf(file_path: str) -> Tuple[Dict[str, Any], List[Tuple[int, str]]]:
        """读取PDF, 返回(文档信息, [(页码, 页面文本), ...])"""
        try:
            reader = PdfReader(file_path)
        except PyPdfError as e:
            raise ValueError(f"PDF解析失败: {file_path}") from e

        if reader.is_encrypted:
            raise ValueError(f"PDF已加密, 无法解析: {file_path}")

        title = (reader.metadata or {}).get("/Title")
        info: Dict[str, Any] = {"pdf_title": str(title)} if title else {}

        pages: List[Tuple[int, str]] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except PyPdfError as e:
                logger.warning("PDF页面文本提取失败", file=file_path, page=page_number, error=str(e))
                continue
            if text.strip():
                pages.append((page_number, text))

        return info, pages

    # ----------------------------------------------------------- 元数据与切块
    @staticmethod
    def _map_front_matter(front_matter: Dict[str, Any], mapping: Dict[str, str]) -> Dict[str, Any]:
        """把 front matter 中真实存在的字段映射成检索元数据

        缺失的字段一律不写入, 避免编造业务默认值(地区/生效日期/产品线等)。
        """
        metadata: Dict[str, Any] = {}
        for source_key, target_key in mapping.items():
            value = front_matter.get(source_key)
            if value is None or value == "":
                continue
            metadata[target_key] = value.isoformat() if hasattr(value, "isoformat") else value
        return metadata

    def _split(self, text: str, base_metadata: Dict[str, Any], source: str) -> List[Document]:
        """把长文本切成文档块, 并补齐来源/块序号等元数据"""
        if not text.strip():
            return []

        chunks = self.text_splitter.create_documents([text], [dict(base_metadata)])
        documents: List[Document] = []

        for index, chunk in enumerate(chunks):
            content = chunk.page_content.strip()
            if not content:
                continue
            metadata = dict(chunk.metadata)
            metadata["source"] = source
            metadata["chunk_index"] = index
            # doc_id 由文件名+页码+块序号构成, 便于回溯引用
            metadata["doc_id"] = f"{source}_{metadata.get('page', 0)}_{index}"
            documents.append(Document(page_content=content, metadata=metadata))

        return documents

    def _build_policy_documents(self, file_path: str) -> List[Document]:
        """解析单个政策文件, 返回文档块列表"""
        path = Path(file_path)
        suffix = path.suffix.lower()
        source = path.stem
        common_metadata = {
            "source_type": "policy",
            "file_name": path.name,
            "file_path": str(path),
        }

        if suffix in SUPPORTED_TEXT_SUFFIXES:
            if suffix == ".md":
                front_matter, body = self._read_markdown(file_path)
                metadata = self._map_front_matter(front_matter, POLICY_FRONT_MATTER_MAPPING)
            else:
                body = self._read_text_file(file_path)
                metadata = {}
            metadata.update(common_metadata)
            return self._split(body, metadata, source)

        if suffix in SUPPORTED_PDF_SUFFIXES:
            info, pages = self._read_pdf(file_path)
            documents: List[Document] = []
            for page_number, page_text in pages:
                metadata = dict(info)
                metadata.update(common_metadata)
                metadata["page"] = page_number
                documents.extend(self._split(page_text, metadata, source))
            return documents

        raise ValueError(f"不支持的政策文件格式: {path.suffix} ({file_path})")

    def _build_manual_documents(self, file_path: str) -> List[Document]:
        """解析单个说明书文件, 返回文档块列表"""
        path = Path(file_path)
        suffix = path.suffix.lower()
        source = path.stem
        common_metadata = {
            "source_type": "manual",
            "file_name": path.name,
            "file_path": str(path),
        }

        if suffix in SUPPORTED_TEXT_SUFFIXES:
            if suffix == ".md":
                front_matter, body = self._read_markdown(file_path)
                metadata = self._map_front_matter(front_matter, MANUAL_FRONT_MATTER_MAPPING)
            else:
                body = self._read_text_file(file_path)
                metadata = {}
            metadata.update(common_metadata)
            return self._split(body, metadata, source)

        if suffix in SUPPORTED_PDF_SUFFIXES:
            info, pages = self._read_pdf(file_path)
            documents: List[Document] = []
            for page_number, page_text in pages:
                metadata = dict(info)
                metadata.update(common_metadata)
                metadata["page"] = page_number
                documents.extend(self._split(page_text, metadata, source))
            return documents

        raise ValueError(f"不支持的说明书文件格式: {path.suffix} ({file_path})")

    # --------------------------------------------------------------- 处理流程
    def process_policy_documents(
        self,
        directory: str = DEFAULT_POLICY_DIR,
        reset: bool = False,
    ) -> List[str]:
        """同步处理政策文档目录并写入向量库"""
        files = self.discover_files(directory)
        if not files:
            raise FileNotFoundError(f"政策文档目录中没有可解析的文件: {directory}")

        if reset:
            self.vector_store.reset_policy_collection()

        documents: List[Document] = []
        failures: List[str] = []

        for file_path in files:
            logger.info("解析政策文件", file=file_path)
            try:
                documents.extend(self._build_policy_documents(file_path))
            except (ValueError, UnicodeDecodeError) as e:
                logger.error("解析政策文件失败", file=file_path, error=str(e))
                failures.append(f"{file_path}: {e}")

        if failures:
            raise ValueError(f"{len(failures)} 个政策文件解析失败: {'; '.join(failures)}")
        if not documents:
            raise ValueError(f"政策文档没有可向量化的文本内容: {directory}")

        ids = self.vector_store.add_policy_documents(documents)
        logger.info("政策文档向量化完成", files=len(files), chunks=len(ids))
        return ids

    def process_manual_documents(
        self,
        directory: str = DEFAULT_MANUAL_DIR,
        reset: bool = False,
    ) -> List[str]:
        """同步处理说明书文档目录并写入向量库"""
        files = self.discover_files(directory)
        if not files:
            raise FileNotFoundError(f"说明书文档目录中没有可解析的文件: {directory}")

        if reset:
            self.vector_store.reset_manual_collection()

        documents: List[Document] = []
        failures: List[str] = []

        for file_path in files:
            logger.info("解析说明书文件", file=file_path)
            try:
                documents.extend(self._build_manual_documents(file_path))
            except (ValueError, UnicodeDecodeError) as e:
                logger.error("解析说明书文件失败", file=file_path, error=str(e))
                failures.append(f"{file_path}: {e}")

        if failures:
            raise ValueError(f"{len(failures)} 个说明书文件解析失败: {'; '.join(failures)}")
        if not documents:
            raise ValueError(f"说明书文档没有可向量化的文本内容: {directory}")

        ids = self.vector_store.add_manual_documents(documents)
        logger.info("说明书文档向量化完成", files=len(files), chunks=len(ids))
        return ids

    async def aprocess_policy_documents(
        self,
        directory: str = DEFAULT_POLICY_DIR,
        reset: bool = False,
    ) -> List[str]:
        """异步处理政策文档目录并写入向量库(真异步)"""
        # 目录扫描是阻塞IO, 交给线程池; 后续入库使用原生异步 embedding
        files = await anyio.to_thread.run_sync(self.discover_files, directory)
        if not files:
            raise FileNotFoundError(f"政策文档目录中没有可解析的文件: {directory}")

        if reset:
            await anyio.to_thread.run_sync(self.vector_store.reset_policy_collection)

        documents: List[Document] = []
        failures: List[str] = []

        for file_path in files:
            logger.info("解析政策文件(异步)", file=file_path)
            try:
                documents.extend(
                    await anyio.to_thread.run_sync(self._build_policy_documents, file_path)
                )
            except (ValueError, UnicodeDecodeError) as e:
                logger.error("解析政策文件失败(异步)", file=file_path, error=str(e))
                failures.append(f"{file_path}: {e}")

        if failures:
            raise ValueError(f"{len(failures)} 个政策文件解析失败: {'; '.join(failures)}")
        if not documents:
            raise ValueError(f"政策文档没有可向量化的文本内容: {directory}")

        ids = await self.vector_store.aadd_policy_documents(documents)
        logger.info("政策文档向量化完成(异步)", files=len(files), chunks=len(ids))
        return ids

    async def aprocess_manual_documents(
        self,
        directory: str = DEFAULT_MANUAL_DIR,
        reset: bool = False,
    ) -> List[str]:
        """异步处理说明书文档目录并写入向量库(真异步)"""
        files = await anyio.to_thread.run_sync(self.discover_files, directory)
        if not files:
            raise FileNotFoundError(f"说明书文档目录中没有可解析的文件: {directory}")

        if reset:
            await anyio.to_thread.run_sync(self.vector_store.reset_manual_collection)

        documents: List[Document] = []
        failures: List[str] = []

        for file_path in files:
            logger.info("解析说明书文件(异步)", file=file_path)
            try:
                documents.extend(
                    await anyio.to_thread.run_sync(self._build_manual_documents, file_path)
                )
            except (ValueError, UnicodeDecodeError) as e:
                logger.error("解析说明书文件失败(异步)", file=file_path, error=str(e))
                failures.append(f"{file_path}: {e}")

        if failures:
            raise ValueError(f"{len(failures)} 个说明书文件解析失败: {'; '.join(failures)}")
        if not documents:
            raise ValueError(f"说明书文档没有可向量化的文本内容: {directory}")

        ids = await self.vector_store.aadd_manual_documents(documents)
        logger.info("说明书文档向量化完成(异步)", files=len(files), chunks=len(ids))
        return ids


# CLI 入口统一收敛的异常类型(目录/文件、编码、格式、向量库、embedding接口)
INGEST_ERRORS = (
    FileNotFoundError,
    PermissionError,
    UnicodeDecodeError,
    ValueError,
    chromadb.errors.ChromaError,
    openai.APIError,
)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description="把 raw_data 下的政策/说明书文档向量化到 ChromaDB")
    parser.add_argument("--policy-dir", default=DEFAULT_POLICY_DIR, help="政策文档目录")
    parser.add_argument("--manual-dir", default=DEFAULT_MANUAL_DIR, help="说明书文档目录")
    parser.add_argument("--reset", action="store_true", help="先清空已有集合再向量化, 避免重复数据")
    return parser.parse_args(argv)


async def _ingest(args: argparse.Namespace) -> int:
    """执行向量化流程, 返回进程退出码"""
    processor = DocumentProcessor()
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
    for collection_name in ("policy_docs", "manual_docs"):
        try:
            print(f"   {collection_name}: {processor.vector_store.get_collection_count(collection_name)}")
        except chromadb.errors.ChromaError as e:
            logger.error("读取集合数量失败", collection=collection_name, error=str(e))
            exit_code = 1

    print("=== 处理结束 ===")
    return exit_code


def main() -> None:
    """文档向量化主流程"""
    args = _parse_args()
    try:
        exit_code = asyncio.run(_ingest(args))
    except KeyboardInterrupt:
        print("已中断")
        sys.exit(130)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
