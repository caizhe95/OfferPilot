"""Knowledge index persistence boundary.

Index construction remains in :mod:`indexer`; this module is the stable place
for persistence exports and can be split into atomic SQL operations incrementally.
"""

from offerpilot.knowledge.indexer import get_index_status

__all__ = ["get_index_status"]
