"""POST /chat API 契约测试。"""

from fastapi.testclient import TestClient

import api.chat_router as chat_router
from agent.state import AgentResponse
from main import app


def test_chat_endpoint_returns_agent_response(monkeypatch):
    async def fake_process_query(query: str, session_id: str, user_id: str | None = None):
        return AgentResponse(
            response=f"echo: {query}",
            query_type="general",
            source_citations=[],
            should_escalate=False,
            processing_time_ms=1.0,
        )

    monkeypatch.setattr(chat_router, "process_query", fake_process_query)
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={"query": "你好", "session_id": "session-api-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["response"] == "echo: 你好"
    assert payload["query_type"] == "general"
    assert payload["source_citations"] == []


def test_chat_endpoint_rejects_empty_query():
    client = TestClient(app)

    response = client.post("/chat", json={"query": "", "session_id": "session-api-2"})

    assert response.status_code == 422
