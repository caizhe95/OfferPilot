"""Knowledge importer: loads parsed Markdown into SQLite + FTS5."""

import sqlite3
from pathlib import Path
from app.knowledge.knowledge_parser import parse_directory
from app.core.config import settings


def import_knowledge(conn: sqlite3.Connection, knowledge_dir: Path | None = None) -> int:
    """Import all knowledge files into the database.

    Returns count of imported entries. Safe to call multiple times (clears existing).
    """
    if knowledge_dir is None:
        knowledge_dir = Path(settings.knowledge_dir)
        if not knowledge_dir.is_absolute():
            knowledge_dir = Path.cwd() / knowledge_dir

    entries = parse_directory(knowledge_dir)
    if not entries:
        return 0

    # Clear existing knowledge
    conn.execute("DELETE FROM knowledge_fts")
    conn.execute("DELETE FROM knowledge")

    count = 0
    for entry in entries:
        cursor = conn.execute(
            "INSERT INTO knowledge (title, content, source, dimension) VALUES (?, ?, ?, ?)",
            (entry["title"], entry["content"], entry["source"], entry["dimension"]),
        )
        row_id = cursor.lastrowid

        # Insert into FTS5
        conn.execute(
            "INSERT INTO knowledge_fts (rowid, title, content, dimension, source) VALUES (?, ?, ?, ?, ?)",
            (row_id, entry["title"], entry["content"], entry["dimension"], entry["source"]),
        )
        count += 1

    conn.commit()
    return count


def search_knowledge(
    conn: sqlite3.Connection,
    query: str,
    dimension: str | None = None,
    limit: int = 5,
    source: str | None = None,
) -> list[dict]:
    """Search knowledge using FTS5 full-text search.

    Args:
        query: Search query string (required, non-empty)
        dimension: Optional dimension filter
        limit: Max results (clamped 1-20)
        source: Optional source filter

    Returns:
        List of matching entries with title, content, source, dimension, score
    """
    if not query or not query.strip():
        raise ValueError("Query must be non-empty")

    limit = max(1, min(limit, 20))

    # Sanitize FTS5 query - remove special characters that break MATCH syntax
    sanitized = _sanitize_fts5_query(query)
    if not sanitized:
        return []

    # Convert to OR-based query for better recall (default FTS5 AND is too strict)
    terms = sanitized.split()
    if len(terms) > 1:
        or_query = " OR ".join(terms)
    else:
        or_query = sanitized

    conditions = ["knowledge_fts MATCH ?"]
    params: list = [or_query]

    if dimension:
        conditions.append("knowledge_fts.dimension = ?")
        params.append(dimension)

    if source:
        conditions.append("knowledge_fts.source = ?")
        params.append(source)

    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT
            knowledge_fts.rowid,
            knowledge_fts.title,
            knowledge_fts.content,
            knowledge_fts.dimension,
            knowledge_fts.source,
            rank AS score
        FROM knowledge_fts
        WHERE {where_clause}
        ORDER BY rank
        LIMIT ?
    """
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    return [
        {
            "title": row["title"],
            "content": row["content"],
            "dimension": row["dimension"],
            "source": row["source"],
            "score": row["score"],
        }
        for row in rows
    ]


def _sanitize_fts5_query(query: str) -> str:
    """Remove FTS5-special characters and filter stopwords from query."""
    import re
    # Remove FTS5 operators
    sanitized = re.sub(r'[*"():?!\-]', ' ', query)
    # Collapse spaces
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    # Filter common English stopwords (keep CJK and meaningful terms)
    stopwords = {
        'what', 'is', 'the', 'a', 'an', 'and', 'or', 'of', 'in', 'to', 'for',
        'how', 'does', 'do', 'can', 'will', 'would', 'should', 'could', 'it',
        'this', 'that', 'are', 'was', 'were', 'be', 'been', 'has', 'have',
        'with', 'on', 'at', 'by', 'from', 'as', 'if', 'then', 'than', 'not',
        'so', 'we', 'you', 'they', 'he', 'she', 'i', 'me', 'my', 'your',
        'about', 'also', 'into', 'no', 'yes', 'just', 'very', 'too', 'all',
    }
    terms = sanitized.split()
    filtered = [t for t in terms if t.lower() not in stopwords]
    return ' '.join(filtered) if filtered else sanitized


def search_knowledge_safe(
    query: str,
    dimension: str | None = None,
    limit: int = 5,
    source: str | None = None,
    trace_id: str | None = None,
) -> list[dict]:
    """Safe wrapper for search_knowledge that manages the DB connection.

    Extracts key terms from query for better FTS5 matching.
    """
    from app.core.database import get_db

    conn = None
    try:
        # Extract meaningful keywords from the query
        # FTS5 MATCH with spaces = AND, so use the question part only
        search_query = query.strip()

        # If combined question+answer, use just the first line/question
        if "\n" in search_query:
            search_query = search_query.split("\n")[0]

        # Limit to reasonable length for FTS5
        if len(search_query) > 200:
            search_query = search_query[:200]

        conn = get_db()
        ensure_knowledge_loaded(conn)
        results = search_knowledge(conn, query=search_query, dimension=dimension, limit=limit, source=source)
        return results
    except Exception as e:
        if trace_id:
            try:
                from app.trace.trace_eval import add_trace_event
                add_trace_event(trace_id, "knowledge_error", 0, {"error": str(e)})
            except Exception:
                pass
        return []
    finally:
        if conn:
            conn.close()


def ensure_knowledge_loaded(conn: sqlite3.Connection) -> int:
    """Load knowledge if the table is empty, otherwise return current count."""
    count = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
    if count == 0:
        return import_knowledge(conn)
    return count
