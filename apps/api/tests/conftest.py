"""Test fixtures for FastAPI tests."""

import os
import pytest
from fastapi.testclient import TestClient

# Override paths before importing app modules
os.environ["SQLITE_PATH"] = "./data/test_offerpilot.db"

# Determine knowledge directory relative to project root
import sys
from pathlib import Path
_project_root = Path(__file__).resolve().parent.parent.parent.parent
_knowledge_dir = _project_root / "knowledge" / "selected"
os.environ["KNOWLEDGE_DIR"] = str(_knowledge_dir)


@pytest.fixture(autouse=True)
def clean_db():
    """Clean up test database before each test."""
    import sqlite3
    from pathlib import Path

    db_path = Path("./data/test_offerpilot.db")
    if db_path.exists():
        db_path.unlink()
    yield
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def client():
    """Create a test client."""
    from app.main import app
    from app.core.database import init_db

    init_db()
    return TestClient(app)
