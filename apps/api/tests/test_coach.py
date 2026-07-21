"""Contract tests for the restricted Coach endpoint and native tool loop."""

from types import SimpleNamespace

from app.llm.llm_client import ToolChatResult
from app.agent.coach_loop import _bounded_history


def test_legacy_product_routes_are_404(client):
    assert client.post("/api/chat", json={"message": "x"}).status_code == 404
    assert client.post("/api/diagnose", json={"question": "q", "answer": "a"}).status_code == 404


def test_coach_streams_final_response(client, monkeypatch):
    calls = iter([ToolChatResult(content="请先说明你的目标方向。", tool_calls=[])])
    monkeypatch.setattr("app.agent.coach_loop.tool_chat_completion", lambda **_: next(calls))
    response = client.post("/api/coach", json={"message": "我想练习技术面试"})
    assert response.status_code == 200
    assert '"type": "final_response"' in response.text
    assert '"termination_reason": "final_response"' in response.text


def test_coach_rejects_unknown_tool_and_recovers(client, monkeypatch):
    calls = iter([
        ToolChatResult(content="", tool_calls=[{"id": "call_1", "name": "read_database", "arguments": "{}"}]),
        ToolChatResult(content="该工具不在允许范围内。", tool_calls=[]),
    ])
    monkeypatch.setattr("app.agent.coach_loop.tool_chat_completion", lambda **_: next(calls))
    response = client.post("/api/coach", json={"message": "读取数据库"})
    assert response.status_code == 200
    assert "unknown_tool" in response.text


def test_coach_approval_resume_continues_same_run(client, monkeypatch):
    responses = iter([
        ToolChatResult(content="", tool_calls=[{"id": "call_save", "name": "save_memory", "arguments": '{"key":"weakness","value":"补充边界条件","category":"diagnosis"}'}]),
        ToolChatResult(content="已保存这条练习偏好，下一次会据此追问。", tool_calls=[]),
    ])
    monkeypatch.setattr("app.agent.coach_loop.tool_chat_completion", lambda **_: next(responses))
    initial = client.post("/api/coach", json={"message": "记住我需要补边界条件"})
    events = [__import__("json").loads(block[6:]) for block in initial.text.split("\n\n") if block.startswith("data: ")]
    approval = next(event for event in events if event["type"] == "permission_required")
    client.post("/api/permission/approve", json={"request_id": approval["request_id"], "session_id": approval["session_id"]})
    resumed = client.post("/api/coach/resume", json={"request_id": approval["request_id"], "session_id": approval["session_id"]})
    assert resumed.status_code == 200
    assert "已保存这条练习偏好" in resumed.text


def test_history_is_bounded_at_message_boundaries():
    messages = [{"role": "user", "content": "x" * 9000}, {"role": "assistant", "content": "y" * 9000}, {"role": "user", "content": "z" * 1000}]
    history = _bounded_history(messages)
    assert history == [{"role": "assistant", "content": "y" * 9000}, {"role": "user", "content": "z" * 1000}]


def test_admin_reindex_requires_key(client):
    assert client.post("/api/admin/knowledge/reindex").status_code == 403


def test_admin_index_status_accepts_configured_key(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "admin_key", "test-admin-key")
    response = client.get("/api/admin/knowledge/status", headers={"X-OfferPilot-Admin-Key": "test-admin-key"})
    assert response.status_code == 200
    assert {"knowledge_count", "embedding_count", "needs_reindex"}.issubset(response.json())
