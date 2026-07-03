"""Built-in data catalog: curated, one-click enterprise datasets.

``definitions`` holds the pure-data catalog; ``seed`` holds the connect/disconnect
engine (imported on demand by the API router to keep this package's import light).
"""

from app.catalog.definitions import CATALOG, CatalogEntry, entry_by_key

__all__ = ["CATALOG", "CatalogEntry", "entry_by_key"]
