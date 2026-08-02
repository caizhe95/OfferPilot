"""Run an explicit knowledge and embedding reindex from the command line."""

import asyncio

from offerpilot.core.database import init_db
from offerpilot.knowledge.knowledge_importer import import_knowledge_with_stats_async


def main() -> None:
    init_db()
    result = asyncio.run(import_knowledge_with_stats_async())
    print({key: result[key] for key in ("imported", "embedded", "reused", "errors")})


if __name__ == "__main__":
    main()
