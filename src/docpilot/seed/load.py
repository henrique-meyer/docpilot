"""Read sample PDFs and hand each one to the admin upload path."""

from __future__ import annotations

from pathlib import Path

from docpilot.documents.service import DocumentService


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
        record = service.begin_upload(data, source=path.name)
        service.finish_upload(record.id, data)
        added.append(path.name)
    return added, skipped
