"""构建脚本的纯逻辑测试：dry-run 不得初始化向量库。"""

import asyncio
import json
from pathlib import Path

import pytest

from scripts import build_knowledge_base as build

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_DIR = PROJECT_ROOT / "raw_data" / "policy_docs"


def test_dry_run_does_not_initialize_vector_store(monkeypatch, tmp_path):
    if not POLICY_DIR.is_dir():
        pytest.skip("raw_data/policy_docs 不存在")

    def _unexpected_vector_store_init():
        raise AssertionError("dry-run 不应初始化 VectorStoreManager")

    monkeypatch.setattr(build, "get_vector_store_manager", _unexpected_vector_store_init)
    args = build._parse_args(
        [
            "--source",
            "policy",
            "--policy-dir",
            str(POLICY_DIR),
            "--dry-run",
            "--report-dir",
            str(tmp_path),
        ]
    )

    exit_code = asyncio.run(build.build_knowledge_base(args))

    assert exit_code == 0
    report = json.loads((tmp_path / "build_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "dry_run"
    assert report["files"]
    assert report["collection_counts"] == {}


def test_empty_directory_writes_failure_report(monkeypatch, tmp_path):
    def _unexpected_vector_store_init():
        raise AssertionError("解析失败时不应初始化 VectorStoreManager")

    monkeypatch.setattr(build, "get_vector_store_manager", _unexpected_vector_store_init)
    empty_dir = tmp_path / "empty_policy"
    empty_dir.mkdir()
    report_dir = tmp_path / "report"
    args = build._parse_args(
        [
            "--source",
            "policy",
            "--policy-dir",
            str(empty_dir),
            "--report-dir",
            str(report_dir),
        ]
    )

    exit_code = asyncio.run(build.build_knowledge_base(args))

    assert exit_code == 1
    report = json.loads((report_dir / "build_report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["errors"]
    assert report["summary"]["failed_files"] == 0
