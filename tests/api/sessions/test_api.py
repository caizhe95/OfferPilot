"""Session API regression tests."""

from __future__ import annotations


def _new_session(client) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 200
    return response.json()["id"]


def test_session_status_filter_separates_active_and_archived_sessions(client):
    active_id = _new_session(client)
    archived_id = _new_session(client)
    assert client.patch(f"/api/sessions/{archived_id}", json={"archived": True}).status_code == 200

    active = client.get("/api/sessions?status=active").json()["sessions"]
    archived = client.get("/api/sessions?status=archived").json()["sessions"]
    all_sessions = client.get("/api/sessions?status=all").json()["sessions"]
    assert active_id in {item["id"] for item in active}
    assert archived_id not in {item["id"] for item in active}
    assert archived_id in {item["id"] for item in archived}
    assert {active_id, archived_id}.issubset({item["id"] for item in all_sessions})


def test_session_detail_is_only_the_base_session_object(client):
    session_id = _new_session(client)
    response = client.get(f"/api/sessions/{session_id}")
    assert response.status_code == 200
    assert "latest_run" not in response.json()
    assert "summary" not in response.json()


def test_archived_session_write_endpoints_are_read_only(client):
    session_id = _new_session(client)
    assert client.patch(f"/api/sessions/{session_id}", json={"archived": True}).status_code == 200

    transcript = client.post(f"/api/sessions/{session_id}/transcript", json={"transcript": "不能写入"})
    assert transcript.status_code == 409
    assert transcript.json()["error"]["code"] == "session_archived_read_only"

    upload = client.post(
        f"/api/sessions/{session_id}/audio-uploads",
        files={"file": ("answer.wav", b"RIFF\x00\x00\x00\x00WAVE\x00\x00\x00\x00", "audio/wav")},
    )
    assert upload.status_code == 409
    assert upload.json()["error"]["code"] == "session_archived_read_only"

    run = client.post(
        f"/api/sessions/{session_id}/runs",
        headers={"Idempotency-Key": "archived-run"},
        json={"type": "coach", "input": {"message": "不能运行"}},
    )
    assert run.status_code == 409
    assert run.json()["error"]["code"] == "session_archived_read_only"


def test_session_cursor_handles_equal_updated_at_without_gaps(client):
    from offerpilot.database.connection import get_db

    created = [_new_session(client) for _ in range(3)]
    conn = get_db()
    try:
        conn.executemany(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            [("2099-01-01T00:00:00+00:00", item) for item in created],
        )
        conn.commit()
    finally:
        conn.close()

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        query = "/api/sessions?status=active&limit=1"
        if cursor:
            query += f"&cursor={cursor}"
        page = client.get(query)
        assert page.status_code == 200
        payload = page.json()
        seen.extend(item["id"] for item in payload["sessions"])
        cursor = payload["next_cursor"]
        if not cursor:
            break

    assert set(created).issubset(seen)
    assert len(seen) == len(set(seen))
    invalid = client.get("/api/sessions?cursor=not-a-valid-cursor")
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_cursor"
