"""Read sample PDFs and hand each one to the admin upload path."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from docpilot.documents.service import DocumentService

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


class SeedError(Exception):
    pass


def seed_set(sample_root: Path, set_name: str, service: DocumentService) -> tuple[list[str], list[str]]:
    """Ingest every PDF in the set. Ready documents with the same file name are skipped."""
    directory = sample_root / set_name
    if not directory.is_dir():
        raise SeedError(f"sample set {set_name} was not found")
    pdfs = sorted(path for path in directory.glob("*.pdf") if path.is_file())
    if not pdfs:
        raise SeedError(f"sample set {set_name} has no PDFs")
    ready = {
        record.source
        for record in service.list_documents()
        if record.status == "ready"
    }
    added: list[str] = []
    skipped: list[str] = []
    for path in pdfs:
        if path.name in ready:
            skipped.append(path.name)
            continue
        data = path.read_bytes()
        title = _title(data) or path.stem
        record = service.begin_upload(data, title=title, source=path.name, year=_year(title))
        service.finish_upload(record.id, data)
        added.append(path.name)
    return added, skipped


def _title(data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data))
        meta = reader.metadata
        title = meta.title if meta is not None else None
    except (PdfReadError, ValueError, OSError):
        return ""
    if not isinstance(title, str):
        return ""
    return " ".join(title.split()).strip()


def _year(title: str) -> str | None:
    match = _YEAR.search(title)
    if match is None:
        return None
    return match.group(0)
