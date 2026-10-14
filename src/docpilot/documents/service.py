"""Upload, replace, and delete source documents.

Search indexing is not connected yet. A stored PDF is `ready`, its chunk count
stays 0, and the 50 MB cap uses extracted text size. Old chunks are removed
only after a new version is stored, which is a no-op until the index exists.
"""

import logging
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from docpilot.config import Settings
from docpilot.documents.errors import DocumentError, MESSAGES
from docpilot.documents.models import AuditEvent, DocumentRecord
from docpilot.documents.pdf import extract_pdf
from docpilot.documents.store import DocumentStore

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class DocumentService:
    def __init__(self, store: DocumentStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._lock = threading.Lock()

    def list_documents(self) -> list[DocumentRecord]:
        return sorted(self.store.list_records(), key=lambda record: record.title.lower())

    def list_audit(self, limit: int = 30) -> list[AuditEvent]:
        events = sorted(self.store.list_audit(), key=lambda event: event.id, reverse=True)
        return events[:limit]

    def index_bytes_used(self, excluding: str | None = None) -> int:
        total = 0
        for record in self.store.list_records():
            if record.id == excluding or record.status != "ready":
                continue
            total += record.index_bytes
        return total

    def source_bytes_used(self) -> int:
        return sum(
            record.source_bytes
            for record in self.store.list_records()
            if record.status == "ready"
        )

    def begin_upload(
        self,
        data: bytes,
        *,
        title: str,
        source: str,
        year: str | None,
    ) -> DocumentRecord:
        self.validate_upload(data)
        record = DocumentRecord(
            id=uuid.uuid4().hex,
            title=_clean(title, fallback=_title_from_source(source)),
            source=_clean(source, fallback="upload.pdf"),
            year=_clean_year(year),
            pages=0,
            chunk_count=0,
            index_bytes=0,
            source_bytes=len(data),
            ingested_at=None,
            status="queued",
        )
        with self._lock:
            self.store.put_record(record)
        return record

    def finish_upload(self, document_id: str, data: bytes) -> None:
        with self._lock:
            record = self.store.get_record(document_id)
            if record is None:
                return
            record.status = "processing"
            record.error = None
            self.store.put_record(record)
            try:
                info = extract_pdf(data)
                self._ensure_capacity(info.index_bytes, excluding=None)
                self.store.put_pdf(document_id, data)
                record.pages = info.pages
                record.chunk_count = 0
                record.index_bytes = info.index_bytes
                record.source_bytes = len(data)
                record.ingested_at = utc_now()
                record.status = "ready"
                record.error = None
                self.store.put_record(record)
                self._audit("upload", record.id, record.title)
            except DocumentError as exc:
                self.store.delete_pdf(document_id)
                record.status = "failed"
                record.error = MESSAGES.get(exc.code, MESSAGES["not_pdf"])
                self.store.put_record(record)
                self._audit("upload_failed", record.id, record.error)
            except Exception:
                logger.exception("upload failed for %s", document_id)
                self.store.delete_pdf(document_id)
                record.status = "failed"
                record.error = "Could not store the document."
                self.store.put_record(record)
                self._audit("upload_failed", record.id, record.error)

    def delete(self, document_id: str) -> None:
        with self._lock:
            record = self.store.get_record(document_id)
            if record is None:
                raise DocumentError("not_found")
            self._delete_indexed_chunks(document_id)
            self.store.delete_pdf(document_id)
            self.store.delete_record(document_id)
            self._audit("delete", document_id, record.title)

    def finish_replace(
        self,
        document_id: str,
        data: bytes,
        *,
        title: str,
        year: str | None,
    ) -> None:
        try:
            self.validate_upload(data)
        except DocumentError as exc:
            self._audit("replace_failed", document_id, MESSAGES.get(exc.code, "Replace failed."))
            return
        with self._lock:
            current = self.store.get_record(document_id)
            if current is None:
                return
            snapshot = replace(current)
            current.status = "processing"
            current.error = None
            self.store.put_record(current)
            try:
                info = extract_pdf(data)
                self._ensure_capacity(info.index_bytes, excluding=document_id)
                # Store the new PDF first. Chunk deletion runs after that so a
                # failed replace leaves the previous source in place. The index
                # hook is empty until search ingestion exists.
                self.store.put_pdf(document_id, data)
                self._delete_indexed_chunks(document_id)
                current.title = _clean(title, fallback=snapshot.title)
                current.year = _clean_year(year) if year is not None else snapshot.year
                current.pages = info.pages
                current.chunk_count = 0
                current.index_bytes = info.index_bytes
                current.source_bytes = len(data)
                current.ingested_at = utc_now()
                current.status = "ready"
                current.error = None
                self.store.put_record(current)
                self._audit("replace", current.id, current.title)
            except DocumentError as exc:
                self.store.put_record(snapshot)
                self._audit("replace_failed", document_id, MESSAGES.get(exc.code, "Replace failed."))
            except Exception:
                logger.exception("replace failed for %s", document_id)
                self.store.put_record(snapshot)
                self._audit("replace_failed", document_id, "Could not store the document.")

    def validate_upload(self, data: bytes) -> None:
        if not data:
            raise DocumentError("empty")
        if len(data) > self.settings.max_upload_bytes:
            raise DocumentError("too_large")
        if not data.startswith(b"%PDF-"):
            raise DocumentError("not_pdf")

    def _ensure_capacity(self, additional: int, excluding: str | None) -> None:
        if self.index_bytes_used(excluding=excluding) + additional > self.settings.index_limit_bytes:
            raise DocumentError("over_capacity")

    def _delete_indexed_chunks(self, document_id: str) -> None:
        """Remove search chunks for this document once the index exists."""
        del document_id

    def _audit(self, action: str, document_id: str | None, detail: str | None) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        event = AuditEvent(
            id=f"{stamp}-{uuid.uuid4().hex[:8]}",
            at=utc_now(),
            actor="admin",
            action=action,
            document_id=document_id,
            detail=detail,
        )
        self.store.put_audit(event)


def _clean(value: str, *, fallback: str) -> str:
    text = " ".join(value.split()).strip()
    if not text:
        text = fallback
    return text[:200]


def _clean_year(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split()).strip()
    if not text:
        return None
    return text[:32]


def _title_from_source(source: str) -> str:
    name = source.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    return name or "Untitled"
