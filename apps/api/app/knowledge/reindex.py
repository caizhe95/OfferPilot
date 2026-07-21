"""Run an explicit knowledge and embedding reindex from the command line."""

from app.core.database import get_db, init_db
from app.knowledge.knowledge_importer import import_knowledge_with_stats


def main() -> None:
    init_db()
    conn = get_db()
    try:
        result = import_knowledge_with_stats(conn)
    finally:
        conn.close()
    print({key: result[key] for key in ("imported", "embedded", "reused", "errors")})


if __name__ == "__main__":
    main()
