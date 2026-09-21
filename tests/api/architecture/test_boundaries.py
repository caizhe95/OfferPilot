"""Static architecture guardrails for the business-oriented Python layout."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = PROJECT_ROOT / "src" / "api" / "offerpilot"
TEST_ROOT = PROJECT_ROOT / "tests" / "api"
OPENAPI_PATHS = {
    "/api/admin/evals/run-all",
    "/api/admin/knowledge/reindex",
    "/api/admin/knowledge/status",
    "/api/admin/operation-logs",
    "/api/admin/runs",
    "/api/admin/runs/{run_id}/events",
    "/api/approvals/{approval_id}/decision",
    "/api/coach/state",
    "/api/coach/reports/export",
    "/api/profile/bootstrap",
    "/api/profile/growth",
    "/api/profile/reset",
    "/api/runs/{run_id}",
    "/api/runs/{run_id}/cancel",
    "/api/runs/{run_id}/events",
    "/api/runs/{run_id}/stream",
    "/api/sessions",
    "/api/sessions/{session_id}",
    "/api/sessions/{session_id}/audio-uploads",
    "/api/sessions/{session_id}/followups",
    "/api/sessions/{session_id}/messages",
    "/api/sessions/{session_id}/reports",
    "/api/sessions/{session_id}/reports/{report_id}",
    "/api/sessions/{session_id}/runs",
    "/api/sessions/{session_id}/summary",
    "/api/sessions/{session_id}/transcript",
    "/health",
    "/ready",
}
LEGACY_IMPORT_PREFIXES = (
    "offerpilot.agent",
    "offerpilot.coaching",
    "offerpilot.harness",
    "offerpilot.permission",
    "offerpilot.runtime",
    "offerpilot.session",
    "offerpilot.trace",
)
REMOVED_SYMBOLS = (
    "ToolCall",
    "ToolResult",
    "AgentConfig",
    "write_audit_log",
)
REMOVED_IMPORTS = ("offerpilot.approvals.audit",)
REMOVED_TABLES = ("permission_grants", "memory_events", "eval_runs")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _is_legacy_import(name: str) -> bool:
    return any(name == prefix or name.startswith(prefix + ".") for prefix in LEGACY_IMPORT_PREFIXES)


def test_source_and_test_modules_stay_within_size_boundaries():
    oversized_source = [
        str(path.relative_to(PROJECT_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if _line_count(path) > 500
    ]
    oversized_tests = [
        str(path.relative_to(PROJECT_ROOT))
        for path in TEST_ROOT.rglob("test_*.py")
        if _line_count(path) > 350
    ]
    assert not oversized_source
    assert not oversized_tests


def test_removed_internal_packages_cannot_be_reintroduced():
    legacy_imports: dict[str, list[str]] = {}
    for path in [*SOURCE_ROOT.rglob("*.py"), *TEST_ROOT.rglob("*.py")]:
        matches = [
            name
            for name in _imports(path)
            if _is_legacy_import(name)
        ]
        if matches:
            legacy_imports[str(path.relative_to(PROJECT_ROOT))] = matches
    assert not legacy_imports


def test_repositories_do_not_depend_on_http_or_feature_services():
    violations: dict[str, list[str]] = {}
    for path in SOURCE_ROOT.rglob("*repository.py"):
        forbidden = [
            name
            for name in _imports(path)
            if name == "fastapi"
            or name.startswith("fastapi.")
            or name.endswith(".api")
            or name.endswith(".service")
        ]
        if forbidden:
            violations[str(path.relative_to(PROJECT_ROOT))] = forbidden
    assert not violations


def test_http_modules_do_not_access_sqlite_or_llm_adapters_directly():
    violations: dict[str, list[str]] = {}
    for path in SOURCE_ROOT.rglob("*api.py"):
        forbidden = [
            name
            for name in _imports(path)
            if name == "offerpilot.database"
            or name.startswith("offerpilot.database.")
            or name == "offerpilot.llm"
            or name.startswith("offerpilot.llm.")
        ]
        if forbidden:
            violations[str(path.relative_to(PROJECT_ROOT))] = forbidden
    assert not violations


def test_public_openapi_paths_stay_frozen():
    from offerpilot.main import app

    assert set(app.openapi()["paths"]) == OPENAPI_PATHS


def test_removed_abstractions_and_tables_stay_out_of_runtime_code():
    violations: dict[str, list[str]] = {}
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        matches = [symbol for symbol in (*REMOVED_SYMBOLS, *REMOVED_TABLES) if symbol in text]
        imports = [name for name in _imports(path) if name in REMOVED_IMPORTS]
        if matches or imports:
            violations[str(path.relative_to(PROJECT_ROOT))] = [*matches, *imports]
    assert not violations
