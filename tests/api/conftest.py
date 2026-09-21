"""Test fixtures for FastAPI tests."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# Override paths before importing app modules
os.environ["OFFERPILOT_SQLITE_PATH"] = "./data/test_offerpilot.db"

# Determine knowledge directory relative to project root
import sys
from pathlib import Path
_project_root = Path(__file__).resolve().parents[2]
_knowledge_dir = _project_root / "resources" / "knowledge"
os.environ["OFFERPILOT_KNOWLEDGE_DIR"] = str(_knowledge_dir)


@pytest.fixture(autouse=True)
def fake_structured_llm(monkeypatch):
    """Provide deterministic structured LLM outputs for offline tests."""
    from offerpilot.core.config import settings

    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "profile_signing_key", "test-profile-signing-key-at-least-32-bytes")
    monkeypatch.setattr(settings, "require_embedding", False)
    # Unit tests must not depend on the developer's real embedding credentials.
    monkeypatch.setattr(settings, "embedding_api_key", "")
    monkeypatch.setattr(settings, "embedding_base_url", "")

    def _content_dimensions(question: str, answer: str) -> dict:
        text = (answer or "").strip()
        length = len(text)
        q_lower = (question or "").lower()
        a_lower = text.lower()
        topic_hit = any(
            token in a_lower
            for token in [
                "react",
                "context",
                "window",
                "tool",
                "llm",
                "agent",
                "token",
                "memory",
                "fts5",
            ]
        )
        aligned = False
        if "react" in q_lower:
            aligned = "react" in a_lower or "推理" in text or "行动" in text
        elif "context" in q_lower or "window" in q_lower or "上下文" in question or "窗口" in question:
            aligned = "context" in a_lower or "window" in a_lower or "上下文" in text or "窗口" in text
        elif "tool" in q_lower:
            aligned = "tool" in a_lower or "工具" in text
        elif "fts5" in q_lower:
            aligned = "fts5" in a_lower or "全文搜索" in text
        else:
            aligned = topic_hit

        if length < 25:
            concept = 3
            structure = 3
            depth = 3
            example = 2
            alignment = 3
        elif length < 80:
            concept = 5
            structure = 5 if any(token in text for token in ["首先", "其次", "最后", "\n\n", "第一", "第二"]) else 4
            depth = 5 if any(token in a_lower for token in ["token", "memory", "sliding", "summarization", "benchmark", "production"]) else 4
            example = 4 if any(token in text for token in ["例如", "比如", "生产环境", "曾经"]) else 3
            alignment = 6 if aligned else 4
        else:
            concept = 8 if topic_hit else 6
            structure = 8 if any(token in text for token in ["首先", "其次", "最后", "\n\n", "第一", "第二"]) else 6
            depth = 8 if any(token in a_lower for token in ["token", "memory", "sliding", "summarization", "benchmark", "production"]) else 6
            example = 7 if any(token in text for token in ["例如", "比如", "生产环境", "曾经"]) else 5
            alignment = 8 if aligned else 5

        return {
            "dimensions": {
                "concept_accuracy": {"score": concept, "explanation": "topic matched" if topic_hit else "topic weak"},
                "structure_completeness": {"score": structure, "explanation": "structured answer" if structure >= 7 else "structure limited"},
                "engineering_depth": {"score": depth, "explanation": "has engineering detail" if depth >= 7 else "depth limited"},
                "example_quality": {"score": example, "explanation": "has concrete example" if example >= 7 else "few examples"},
                "question_alignment": {"score": alignment, "explanation": "aligned with question" if alignment >= 7 else "alignment limited"},
            }
        }

    def _voice_dimensions(transcript: str) -> dict:
        text = transcript or ""
        lowered = text.lower()
        filler_terms = ["嗯", "呃", "啊", "就是", "就是说", "那个", "这个", "对吧", "um", "uh", "like", "you know"]
        filler_count = sum(lowered.count(term) for term in filler_terms)
        repeated = max(text.count("就是"), text.count("嗯"), text.count("那个"))
        length = len(text)

        if length < 25:
            fluency = 4
            filler_words = 6
            redundancy = 5
            spoken_clarity = 4
            answer_pacing = 4
        else:
            fluency = 8 if filler_count <= 1 else 4 if filler_count <= 4 else 2
            filler_words = 8 if filler_count <= 1 else 5 if filler_count <= 4 else 2
            redundancy = 8 if repeated <= 1 else 5 if repeated <= 3 else 3
            spoken_clarity = 8 if len(text) >= 40 else 5
            answer_pacing = 8 if text.count("。") >= 2 or text.count("\n") >= 2 else 5

        return {
            "dimensions": {
                "fluency": {"score": fluency, "explanation": "fluency"},
                "filler_words": {"score": filler_words, "explanation": "filler words"},
                "redundancy": {"score": redundancy, "explanation": "redundancy"},
                "spoken_clarity": {"score": spoken_clarity, "explanation": "clarity"},
                "answer_pacing": {"score": answer_pacing, "explanation": "pacing"},
            }
        }

    def _diagnosis(question: str, answer: str, scorable_points: list[dict]) -> dict:
        evidence = (answer or "").strip()[:80]
        content = _content_dimensions(question, answer)["dimensions"]
        voice = _voice_dimensions(answer)["dimensions"]
        points = scorable_points or [{"id": "test-default-point", "label": "围绕题目说明核心概念"}]
        return {
            "exam_points": [
                {
                    "point_id": point["id"],
                    "status": "covered" if evidence else "missing",
                    "evidence": evidence or None,
                    "explanation": "回答已提供可核验的表述" if evidence else "回答未提供有效内容",
                }
                for point in points
            ],
            "content_dimensions": content,
            "voice_dimensions": voice,
            "improvements": ["补充具体工程场景和边界条件。"],
        }

    def fake_structured_json_completion(*, task_name: str, user_payload: dict, **kwargs):
        if task_name == "interview_diagnosis":
            data = _diagnosis(
                str(user_payload.get("question", "")),
                str(user_payload.get("candidate_answer", "")),
                list(user_payload.get("scorable_exam_points") or []),
            )
        else:
            data = {}

        return SimpleNamespace(
            data=data,
            source="llm",
            raw_text="{}",
            error="",
        )

    monkeypatch.setattr(
        "offerpilot.diagnosis.scoring.structured_json_completion",
        fake_structured_json_completion,
        raising=True,
    )

@pytest.fixture(autouse=True)
def clean_db():
    """Clean up the Windows SQLite test file, tolerating brief close races."""
    from offerpilot.core.config import settings

    db_path = settings.db_path
    _delete_test_database(db_path)
    yield
    _delete_test_database(db_path)


def _delete_test_database(db_path: Path) -> None:
    for attempt in range(10):
        try:
            db_path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))


@pytest.fixture
def client():
    """Create a test client."""
    from offerpilot.main import app
    from offerpilot.database.connection import init_db

    init_db()
    with TestClient(app) as client:
        from offerpilot.profiles.cookies import PROFILE_COOKIE, make_profile_cookie

        profile_id = "00000000-0000-4000-8000-000000000001"
        client.cookies.set(PROFILE_COOKIE, make_profile_cookie(profile_id))
        client.headers.update({"Origin": "http://localhost:3000"})
        yield client
