"""真实主图冒烟测试。

默认跳过；构建好知识库并配置 LLM 后执行：
    RUN_AGENT_EVAL=1 python -m pytest tests/integration/test_agent_graph.py -q
"""

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from agent.main_graph import process_query
from rag.vector_store import get_vector_store_manager

GOLDEN_SET = Path(__file__).resolve().parents[2] / "rag" / "eval" / "golden_set.jsonl"


def _load_golden_question(row_id: str) -> dict[str, Any]:
    with GOLDEN_SET.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["id"] == row_id:
                return row
    raise ValueError(f"golden_set 中不存在题目: {row_id}")


def test_policy_question_returns_response_and_real_citation():
    if os.getenv("RUN_AGENT_EVAL") != "1":
        pytest.skip("设置 RUN_AGENT_EVAL=1 并先构建知识库后执行")
    if get_vector_store_manager().get_collection_count("policy_docs") == 0:
        pytest.skip("policy_docs 集合为空，请先运行构建脚本")

    row = _load_golden_question("keg-AS-003")
    result = asyncio.run(
        process_query(
            user_query=row["question"],
            session_id=f"agent-eval-{uuid.uuid4()}",
        )
    )

    assert result.query_type == "policy"
    assert result.response
    assert result.source_citations
    assert result.source_citations[0].source.source_type == "policy"
