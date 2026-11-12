"""知识库构建脚本。

用法（项目根目录）：
    python -m scripts.build_knowledge_base --source all
    python -m scripts.build_knowledge_base --source policy --dry-run
    python -m scripts.build_knowledge_base --reset --source policy

流程：
1. 解析选中的真实文档；任一文件失败则只生成报告、不写向量库；
2. 生成稳定的 chunk_id（document_id::page::chunk_index）；
3. 先 upsert，再删除该 document_id 的旧块，保证重复构建幂等；
4. 无论成功或失败都写入 reports/knowledge_base/build_report.json 与 quality_report.json。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio.to_thread
import chromadb
import openai

from core.logger import logger
from rag.document_parser import DEFAULT_REPORT_DIR, ParsedDocument
from rag.document_processor import (
    DEFAULT_MANUAL_DIR,
    DEFAULT_POLICY_DIR,
    DocumentProcessor,
)
from rag.vector_store import (
    MANUAL_COLLECTION_NAME,
    POLICY_COLLECTION_NAME,
    VectorStoreManager,
    get_vector_store_manager,
)

PROJECT_ROOT = Path(__file__).parent.parent

SOURCE_COLLECTIONS = {
    "policy": POLICY_COLLECTION_NAME,
    "manual": MANUAL_COLLECTION_NAME,
}

BUILD_ERRORS = (
    FileNotFoundError,
    PermissionError,
    UnicodeDecodeError,
    ValueError,
    OSError,
    chromadb.errors.ChromaError,
    openai.APIError,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finish_report(report: dict[str, Any]) -> None:
    report["finished_at"] = _now()
    started_at = datetime.fromisoformat(report["started_at"])
    finished_at = datetime.fromisoformat(report["finished_at"])
    report["elapsed_seconds"] = round((finished_at - started_at).total_seconds(), 2)


def _display_path(path: str | Path) -> str:
    source = Path(path)
    try:
        return str(source.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(source)


def _chunk_lengths(documents: list[Any]) -> dict[str, int]:
    lengths = [len(document.page_content) for document in documents]
    if not lengths:
        return {"min": 0, "max": 0, "avg": 0}
    return {
        "min": min(lengths),
        "max": max(lengths),
        "avg": round(sum(lengths) / len(lengths), 2),
    }


def _skipped_pages(parsed: ParsedDocument) -> list[dict[str, Any]]:
    return [
        {
            "page": quality.page,
            "issues": list(quality.issues),
            "reason": quality.skip_reason,
            "text_length": quality.text_length,
            "source": quality.source,
            "ocr_confidence": quality.ocr_confidence,
        }
        for quality in parsed.page_qualities
        if not quality.indexed
    ]


def _quality_pages(file_path: str, parsed: ParsedDocument) -> dict[str, Any]:
    return {
        "file_path": _display_path(file_path),
        "source_type": parsed.source_type,
        "pages": [
            {
                "page": quality.page,
                "text_length": quality.text_length,
                "source": quality.source,
                "ocr_confidence": quality.ocr_confidence,
                "issues": list(quality.issues),
                "indexed": quality.indexed,
                "skip_reason": quality.skip_reason,
            }
            for quality in parsed.page_qualities
        ],
    }


def _write_report(report_dir: str | Path, filename: str, payload: dict[str, Any]) -> Path:
    directory = Path(report_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def _parse_files(
    processor: DocumentProcessor,
    selections: list[tuple[str, str]],
    report: dict[str, Any],
    quality_files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], ParsedDocument, list[Any]]]]:
    """解析所有选中文件；返回报告条目和可写入的成功结果。"""
    file_results: list[dict[str, Any]] = []
    successful: list[tuple[dict[str, Any], ParsedDocument, list[Any]]] = []

    for source_type, directory in selections:
        try:
            files = await anyio.to_thread.run_sync(processor.discover_files, directory)
        except BUILD_ERRORS as e:
            logger.error("扫描目录失败", directory=directory, error=str(e))
            report["errors"].append({"file_path": directory, "error": str(e)})
            continue

        if not files:
            message = f"目录中没有可解析文件: {directory}"
            logger.error(message)
            report["errors"].append({"file_path": directory, "error": message})
            continue

        for file_path in files:
            try:
                if source_type == "policy":
                    documents, parsed = await anyio.to_thread.run_sync(
                        processor.build_policy_documents, file_path
                    )
                else:
                    documents, parsed = await anyio.to_thread.run_sync(
                        processor.build_manual_documents, file_path
                    )
            except BUILD_ERRORS as e:
                logger.error("构建文件失败", file=file_path, error=str(e))
                result = {
                    "file_path": _display_path(file_path),
                    "source_type": source_type,
                    "collection": SOURCE_COLLECTIONS[source_type],
                    "status": "failed",
                    "chunks": 0,
                    "chunk_lengths": {"min": 0, "max": 0, "avg": 0},
                    "pages_total": 0,
                    "pages_indexed": 0,
                    "pages_skipped": [],
                    "error": str(e),
                }
                file_results.append(result)
                report["errors"].append({"file_path": _display_path(file_path), "error": str(e)})
                continue

            result = {
                "file_path": _display_path(file_path),
                "source_type": source_type,
                "collection": SOURCE_COLLECTIONS[source_type],
                "status": "success",
                "document_id": parsed.doc_metadata.get("document_id"),
                "chunks": len(documents),
                "chunk_lengths": _chunk_lengths(documents),
                "pages_total": len(parsed.page_qualities),
                "pages_indexed": sum(1 for quality in parsed.page_qualities if quality.indexed),
                "pages_skipped": _skipped_pages(parsed),
                "error": None,
            }
            file_results.append(result)
            successful.append((result, parsed, documents))
            quality_files.append(_quality_pages(file_path, parsed))

    return file_results, successful



def _validate_unique_ids(
    successful: list[tuple[dict[str, Any], ParsedDocument, list[Any]]],
    report: dict[str, Any],
) -> None:
    document_keys: set[tuple[str, str]] = set()
    chunk_ids: set[tuple[str, str]] = set()

    for result, parsed, documents in successful:
        source_type = parsed.source_type
        document_id = str(parsed.doc_metadata.get("document_id") or "")
        document_key = (source_type, document_id)
        if document_key in document_keys:
            report["errors"].append(
                {
                    "file_path": result["file_path"],
                    "error": f"{source_type} 集合内 document_id 重复: {document_id}",
                }
            )
        document_keys.add(document_key)

        for document in documents:
            chunk_key = (source_type, str(document.metadata["chunk_id"]))
            if chunk_key in chunk_ids:
                report["errors"].append(
                    {
                        "file_path": result["file_path"],
                        "error": f"{source_type} 集合内 chunk_id 重复: {chunk_key[1]}",
                    }
                )
            chunk_ids.add(chunk_key)


def _duplicate_content_count(
    successful: list[tuple[dict[str, Any], ParsedDocument, list[Any]]],
) -> int:
    seen: dict[str, int] = {}
    for _, _, documents in successful:
        for document in documents:
            digest = hashlib.sha256(document.page_content.encode("utf-8")).hexdigest()
            seen[digest] = seen.get(digest, 0) + 1
    return sum(count - 1 for count in seen.values() if count > 1)


def _summary(
    file_results: list[dict[str, Any]],
    successful: list[tuple[dict[str, Any], ParsedDocument, list[Any]]],
    report: dict[str, Any],
) -> dict[str, Any]:
    all_documents = [
        document
        for _, _, documents in successful
        for document in documents
    ]
    return {
        "files_total": len(file_results),
        "failed_files": sum(1 for result in file_results if result["status"] == "failed"),
        "successful_files": sum(1 for result in file_results if result["status"] == "success"),
        "chunks_total": sum(result["chunks"] for result in file_results),
        "chunk_lengths": _chunk_lengths(all_documents),
        "duplicate_content_chunks": _duplicate_content_count(successful),
        "errors": len(report["errors"]),
    }


async def _write_to_vector_store(
    manager: VectorStoreManager,
    successful: list[tuple[dict[str, Any], ParsedDocument, list[Any]]],
    *,
    reset: bool,
    report: dict[str, Any],
) -> None:
    """解析全部成功后才进入写入阶段。"""
    selected_collections = {
        SOURCE_COLLECTIONS[parsed.source_type] for _, parsed, _ in successful
    }
    if reset:
        for collection_name in selected_collections:
            if collection_name == POLICY_COLLECTION_NAME:
                await anyio.to_thread.run_sync(manager.reset_policy_collection)
            else:
                await anyio.to_thread.run_sync(manager.reset_manual_collection)

    counts_before: dict[str, int] = {}
    for collection_name in selected_collections:
        counts_before[collection_name] = await anyio.to_thread.run_sync(
            manager.get_collection_count, collection_name
        )

    for result, parsed, documents in successful:
        collection_name = SOURCE_COLLECTIONS[parsed.source_type]
        ids = [str(document.metadata["chunk_id"]) for document in documents]
        if parsed.source_type == "policy":
            await manager.aupsert_policy_documents(documents, ids)
        else:
            await manager.aupsert_manual_documents(documents, ids)

        document_id = str(parsed.doc_metadata["document_id"])
        stale_ids = await anyio.to_thread.run_sync(
            manager.delete_stale_ids, collection_name, document_id, ids
        )
        result["upserted_chunks"] = len(ids)
        result["stale_deleted"] = len(stale_ids)

    counts_after: dict[str, int] = {}
    for collection_name in selected_collections:
        counts_after[collection_name] = await anyio.to_thread.run_sync(
            manager.get_collection_count, collection_name
        )
    report["collection_counts"] = {
        collection_name: {
            "before": counts_before[collection_name],
            "after": counts_after[collection_name],
        }
        for collection_name in sorted(selected_collections)
    }



def _build_selections(source: str, policy_dir: str, manual_dir: str) -> list[tuple[str, str]]:
    if source == "policy":
        return [("policy", policy_dir)]
    if source == "manual":
        return [("manual", manual_dir)]
    return [("policy", policy_dir), ("manual", manual_dir)]


def _write_reports(
    report_dir: str | Path,
    report: dict[str, Any],
    quality_files: list[dict[str, Any]],
) -> tuple[Path, Path]:
    build_path = _write_report(report_dir, "build_report.json", report)
    quality_path = _write_report(
        report_dir,
        "quality_report.json",
        {"generated_at": _now(), "files": quality_files},
    )
    return build_path, quality_path


async def build_knowledge_base(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "started_at": _now(),
        "finished_at": None,
        "status": "running",
        "dry_run": args.dry_run,
        "source": args.source,
        "reset": args.reset,
        "report_dir": _display_path(args.report_dir),
        "errors": [],
        "files": [],
        "summary": {},
        "collection_counts": {},
    }
    quality_files: list[dict[str, Any]] = []

    try:
        processor = DocumentProcessor(
            ocr_fallback=args.ocr_fallback,
            caption_images=args.caption_images,
            vision_model=args.vision_model,
            chunk_size=args.max_chars,
            overlap=args.overlap,
            report_dir=args.report_dir,
        )
    except BUILD_ERRORS as e:
        logger.error("初始化解析器失败", error=str(e))
        report["status"] = "failed"
        report["errors"].append({"error": str(e)})
        _finish_report(report)
        _write_reports(args.report_dir, report, quality_files)
        return 1

    selections = _build_selections(args.source, args.policy_dir, args.manual_dir)
    file_results, successful = await _parse_files(
        processor, selections, report, quality_files
    )
    report["files"] = file_results
    _validate_unique_ids(successful, report)
    report["summary"] = _summary(file_results, successful, report)

    if report["errors"]:
        report["status"] = "failed"
        _finish_report(report)
        _write_reports(args.report_dir, report, quality_files)
        logger.error("知识库构建失败, 未写入向量库", errors=len(report["errors"]))
        return 1

    if args.dry_run:
        report["status"] = "dry_run"
        _finish_report(report)
        _write_reports(args.report_dir, report, quality_files)
        print(f"dry-run 完成: {report['summary']['chunks_total']} 个块, 未写入向量库")
        return 0

    try:
        manager = await anyio.to_thread.run_sync(get_vector_store_manager)
        await _write_to_vector_store(manager, successful, reset=args.reset, report=report)
    except BUILD_ERRORS as e:
        logger.error("写入向量库失败", error=str(e))
        report["status"] = "failed"
        report["errors"].append({"error": str(e)})
        _finish_report(report)
        _write_reports(args.report_dir, report, quality_files)
        return 1

    report["status"] = "success"
    _finish_report(report)
    build_path, quality_path = _write_reports(args.report_dir, report, quality_files)
    print(
        f"构建完成: {report['summary']['chunks_total']} 个块, "
        f"报告: {build_path}, {quality_path}"
    )
    return 0



def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建政策/说明书知识库")
    parser.add_argument("--source", choices=["all", "policy", "manual"], default="all")
    parser.add_argument("--policy-dir", default=DEFAULT_POLICY_DIR)
    parser.add_argument("--manual-dir", default=DEFAULT_MANUAL_DIR)
    parser.add_argument("--max-chars", type=int, default=500)
    parser.add_argument("--overlap", type=int, default=50)
    parser.add_argument("--reset", action="store_true", help="只重置 --source 选中的集合")
    parser.add_argument("--dry-run", action="store_true", help="只解析并生成报告, 不写向量库")
    parser.add_argument("--ocr-fallback", action="store_true")
    parser.add_argument("--caption-images", action="store_true")
    parser.add_argument("--vision-model", default=None)
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    return parser.parse_args(argv)


def main() -> None:
    """CLI 入口。"""
    args = _parse_args()
    try:
        exit_code = asyncio.run(build_knowledge_base(args))
    except KeyboardInterrupt:
        print("已中断")
        sys.exit(130)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

