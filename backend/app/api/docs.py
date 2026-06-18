"""In-app documentation browser.

Serves the repository's ``docs/*.md`` as JSON so the frontend ``/docs`` page can
render them. Strictly read-only. The directory is resolved from
``settings.docs_path`` (default ``<repo>/docs`` in dev, ``/docs`` inside the
container image — mount ``./docs:/docs:ro`` in docker-compose). Degrades
gracefully: when the directory is missing the list is empty and lookups 404.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from app import models, schemas
from app.config import get_settings
from app.security import get_current_user

router = APIRouter(prefix="/docs", tags=["docs"])

# Slugs are markdown filenames without their extension. Keep this strict: it is
# the first line of defense against path traversal (the resolved-parent check
# below is the second).
_SLUG_RE = re.compile(r"[A-Za-z0-9._-]+")


def _docs_dir() -> Path:
    return get_settings().docs_path


def _title_of(text: str, fallback: str) -> str:
    """First markdown H1 (``# ...``), else the fallback (the slug)."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
        if stripped and not stripped.startswith("#"):
            break  # prose before any heading — don't scan the whole file
    return fallback


def _summary(path: Path, text: str, stat) -> schemas.DocSummary:
    return schemas.DocSummary(
        slug=path.stem,
        title=_title_of(text, path.stem),
        size_bytes=stat.st_size,
        updated_at=datetime.fromtimestamp(stat.st_mtime, UTC),
    )


@router.get("", response_model=list[schemas.DocSummary])
def list_docs(_: models.User = Depends(get_current_user)) -> list[schemas.DocSummary]:
    root = _docs_dir()
    if not root.is_dir():
        return []
    out: list[schemas.DocSummary] = []
    for path in root.glob("*.md"):
        if not path.is_file():
            continue
        try:
            out.append(_summary(path, path.read_text(encoding="utf-8"), path.stat()))
        except OSError:
            continue  # unreadable file — skip rather than fail the whole list
    out.sort(key=lambda d: d.title.lower())
    return out


@router.get("/{slug}", response_model=schemas.DocContent)
def get_doc(slug: str, _: models.User = Depends(get_current_user)) -> schemas.DocContent:
    root = _docs_dir()
    path = root / f"{slug}.md"
    # Reject anything that isn't a plain filename living directly in docs_dir.
    if (
        not _SLUG_RE.fullmatch(slug)
        or ".." in slug
        or not root.is_dir()
        or path.resolve().parent != root.resolve()
        or not path.is_file()
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        text = path.read_text(encoding="utf-8")
        stat = path.stat()
    except OSError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found") from exc
    summary = _summary(path, text, stat)
    return schemas.DocContent(**summary.model_dump(), markdown=text)
