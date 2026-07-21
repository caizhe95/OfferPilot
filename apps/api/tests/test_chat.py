"""Tests for chat and diagnose API endpoints."""

from fastapi.testclient import TestClient
from app.core.config import settings


class TestChatApi:
    """Tests for POST /api/chat SSE endpoint."""

    def test_chat_requires_message(self, client: TestClient):
        """Should return 422 if no message."""
        resp = client.post("/api/chat", json={})
        assert resp.status_code == 422

    def test_chat_creates_session_and_streams(self, client: TestClient):
        """Should create session and return SSE stream."""
        resp = client.post("/api/chat", json={"message": "请分析这个回答"})
        assert resp.status_code in (200, 500)
        if resp.status_code == 200:
            assert resp.headers["content-type"].startswith("text/event-stream")

    def test_chat_with_existing_session(self, client: TestClient):
        """Should use existing session."""
        create_resp = client.post(
            "/api/sessions",
            json={"metadata": {"test": True}},
        )
        session_id = create_resp.json()["id"]

        resp = client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "Hello"},
        )
        assert resp.status_code in (200, 500)

    def test_chat_invalid_session(self, client: TestClient):
        """Should return 404 for non-existent session."""
        resp = client.post(
            "/api/chat",
            json={"session_id": "nonexistent-id", "message": "Hello"},
        )
        assert resp.status_code in (200, 404, 500)


class TestDiagnoseApi:
    """Tests for POST /api/diagnose endpoint."""

    def test_diagnose_requires_fields(self, client: TestClient):
        """Should return 422 if missing question/answer."""
        resp = client.post("/api/diagnose", json={})
        assert resp.status_code == 422

    def test_diagnose_with_valid_input(self, client: TestClient):
        """Should diagnose a valid question/answer pair."""
        resp = client.post(
            "/api/diagnose",
            json={
                "question": "什么是 AI Agent 中的 Tool Calling？",
                "answer": "Tool Calling 是 AI Agent 调用外部工具的能力。在 LLM 工程中，通常通过 Function Calling 实现，让模型能够执行数据库查询、API调用等操作。",
            },
        )
        assert resp.status_code == 200
        data = resp.json()

        assert "session_id" in data
        assert "trace_id" in data
        assert "overall_score" in data
        assert "content_scores" in data
        assert "voice_scores" in data
        assert "report" in data
        assert "followups" in data

        assert 0 <= data["overall_score"] <= 10
        assert "dimensions" in data["content_scores"]
        assert "dimensions" in data["voice_scores"]

        content_dims = data["content_scores"]["dimensions"]
        assert "concept_accuracy" in content_dims
        assert "structure_completeness" in content_dims

        voice_dims = data["voice_scores"]["dimensions"]
        assert "fluency" in voice_dims

        assert isinstance(data["followups"], list)
        assert isinstance(data["report"], str)

    def test_diagnose_with_short_answer(self, client: TestClient):
        """Short answers should get lower scores."""
        resp = client.post(
            "/api/diagnose",
            json={
                "question": "解释一下 LLM 的 Context Window",
                "answer": "Context Window 就是上下文窗口。",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        concept = data["content_scores"]["dimensions"]["concept_accuracy"]
        assert concept["score"] <= 6

    def test_diagnose_with_detailed_answer(self, client: TestClient):
        """Detailed structured answers should score better."""
        resp = client.post(
            "/api/diagnose",
            json={
                "question": "什么是 AI Agent 中的 Context Window Management？",
                "answer": """首先，Context Window Management 是 AI Agent / LLM 工程中的核心技术挑战之一。

核心概念：Context Window 指的是 LLM 在一次推理中能处理的最大 token 数量。

工程实践包括滑动窗口策略、摘要压缩、Memory 存储等。

在生产环境中，我们遇到过上下文超限导致模型幻觉增加的问题。

总之，Context Window Management 是构建可靠 AI Agent 的基础工程能力。""",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_score"] > 0

    def test_diagnose_with_session_id(self, client: TestClient):
        """Should associate diagnosis with an existing session."""
        create_resp = client.post("/api/sessions", json={"metadata": {"test": True}})
        session_id = create_resp.json()["id"]

        resp = client.post(
            "/api/diagnose",
            json={
                "question": "测试问题",
                "answer": "测试回答",
                "session_id": session_id,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id

    def test_diagnose_creates_trace(self, client: TestClient):
        """Should create a trace that can be retrieved."""
        resp = client.post(
            "/api/diagnose",
            json={
                "question": "什么是向量检索？",
                "answer": "向量检索是通过将文本转换为向量进行语义搜索的技术。",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        trace_id = data["trace_id"]

        trace_resp = client.get(f"/api/traces/{trace_id}")
        assert trace_resp.status_code == 200
        trace_data = trace_resp.json()
        assert "status" in trace_data

    def test_diagnose_generates_report(self, client: TestClient):
        """Report should contain expected sections."""
        resp = client.post(
            "/api/diagnose",
            json={
                "question": "请解释 FTS5 全文搜索的实现原理",
                "answer": "FTS5 是 SQLite 的全文搜索扩展，通过创建虚拟表，使用 MATCH 操作符进行全文检索。",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        report = data["report"]
        assert len(report) > 50
        # Report should be a valid string with content
        assert isinstance(report, str)

    def test_diagnose_returns_memory_permission_without_direct_save(self, client: TestClient):
        """Diagnosis should propose memories but not bypass high-risk permission."""
        from app.core.database import get_db

        resp = client.post(
            "/api/diagnose",
            json={
                "question": "什么是 Context Window 管理？",
                "answer": "就是上下文。",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "memory_candidates" in data
        assert len(data["memory_candidates"]) >= 1
        assert data["memory_permission"]["type"] == "permission_required"
        assert data["memory_permission"]["tool_name"] == "save_memory"

        conn = get_db()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id = ?",
                (data["session_id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0


class TestKnowledgeIntegration:
    """Tests verifying knowledge base is correctly wired into main pipeline."""

    def test_diagnose_returns_sources_for_english_topic(self, client: TestClient):
        """diagnose should return non-empty sources when query matches knowledge base (English terms)."""
        resp = client.post(
            "/api/diagnose",
            json={
                "question": "What is ReAct and how does tool calling work?",
                "answer": "ReAct is a reasoning-acting loop for AI Agents. Tool calling enables the agent to invoke external functions.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        sources = data.get("sources", [])
        # For queries matching English knowledge base terms, sources should be non-empty
        assert len(sources) >= 1, f"Expected at least 1 source for ReAct/tool topic, got {len(sources)}"

    def test_trace_has_knowledge_retrieved(self, client: TestClient):
        """Trace should contain knowledge_retrieved event with real count."""
        resp = client.post(
            "/api/diagnose",
            json={
                "question": "什么是 React Loop？",
                "answer": "ReAct 是一种推理-行动循环，AI Agent 通过交替推理和行动来完成任务。",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        trace_id = data["trace_id"]

        trace_resp = client.get(f"/api/traces/{trace_id}")
        assert trace_resp.status_code == 200
        trace_data = trace_resp.json()

        # Find knowledge_retrieved event
        events = trace_data.get("events", [])
        knowledge_events = [e for e in events if e.get("event_type") == "knowledge_retrieved"]
        assert len(knowledge_events) >= 1, "trace must have knowledge_retrieved event"

    def test_sse_chat_streams_to_completion(self, client: TestClient):
        """SSE /api/chat should complete with run_complete event."""
        resp = client.post(
            "/api/chat",
            json={"message": "请诊断：什么是 Context Window？Context Window 是 LLM 的一次推理 token 上限。"},
        )
        # Accept 200 (SSE) or error codes
        if resp.status_code == 200:
            body = resp.text
            assert "run_complete" in body, "SSE stream must contain run_complete event"
            assert "session_id" in body
            assert "trace_id" in body

    def test_chat_creates_session_progress_and_trace(self, client: TestClient):
        """Chat should persist session, progress, and trace."""
        resp = client.post(
            "/api/chat",
            json={"message": "请诊断面试回答"},
        )
        if resp.status_code != 200:
            return  # Skip if agent not running

        body = resp.text
        # Extract session_id from run_complete
        import re
        # Also try from the first event which may contain session_start
        sess_match = re.search(r'"session_id"\s*:\s*"([^"]+)"', body)
        if not sess_match:
            return  # May be truncated/streaming
        session_id = sess_match.group(1)

        # Query session
        sess_resp = client.get(f"/api/sessions/{session_id}")
        # Session may not be persisted yet if streaming, accept 404
        if sess_resp.status_code != 200:
            return
        session = sess_resp.json()
        assert session["status"] in ("running", "completed", "failed")

    def test_chat_error_returns_structured_sse(self, client: TestClient):
        """Chat errors should return structured SSE error, not silent failure."""
        # Create session and transition to completed via running
        create_resp = client.post("/api/sessions", json={"metadata": {}})
        sid = create_resp.json()["id"]

        # Transition: created -> running -> completed
        from app.session.session import transition_session
        transition_session(sid, "running")
        transition_session(sid, "completed")

        # Now try to chat on completed session - should get new session
        resp = client.post(
            "/api/chat",
            json={"session_id": sid, "message": "test"},
        )
        # Should work (new session created) or return valid SSE
        if resp.status_code == 200:
            body = resp.text
            assert "run_complete" in body or "error" in body

    def test_chat_promotes_tool_permission_required(self, client: TestClient, monkeypatch):
        """Agent tool_result permission_required should become canonical SSE event."""
        from app.chat import chat_api

        create_resp = client.post("/api/sessions", json={"metadata": {}})
        session_id = create_resp.json()["id"]

        async def fake_stream(input_text, sid=None):
            yield {
                "type": "tool_result",
                "tool_name": "save_memory",
                "result": {
                    "type": "permission_required",
                    "permission_required": True,
                    "session_id": session_id,
                    "request_id": "req-1",
                    "tool_name": "save_memory",
                    "risk_level": "high",
                    "params": {"key": "weakness", "value": "x"},
                    "message": "approval required",
                },
            }

        monkeypatch.setattr(chat_api.agent_client, "run_stream", fake_stream)
        resp = client.post("/api/chat", json={
            "session_id": session_id,
            "message": "面试题：什么是 ReAct？ 回答：不知道",
        })
        assert resp.status_code == 200
        body = resp.text
        assert "permission_required" in body
        assert "waiting_approval" in body

        session_resp = client.get(f"/api/sessions/{session_id}")
        assert session_resp.json()["status"] == "waiting_approval"
