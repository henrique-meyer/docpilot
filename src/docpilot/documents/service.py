"""Upload, replace, and delete source documents.

A stored PDF is chunked and upserted. When Azure AI Search is not configured,
chunks stay in a process-local index. Old chunk ids are removed only after the
new chunks are upserted. The 50 MB cap uses extracted text size.
"""

import logging
import threading
import uuid
from datetime import UTC, datetime

from docpilot.config import Settings
from docpilot.documents.describe import read_title, read_year
from docpilot.documents.errors import MESSAGES, DocumentError
from docpilot.documents.models import AuditEvent, DocumentRecord
from docpilot.documents.pdf import extract_pdf
from docpilot.documents.store import DocumentStore
from docpilot.ingest.pipeline import Ingestor, MemoryIndex

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class DocumentService:
    def __init__(
        self,
        store: DocumentStore,
        settings: Settings,
        ingestor: Ingestor | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.ingestor = ingestor or Ingestor(None, MemoryIndex())
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
        return sum(record.source_bytes for record in self.store.list_records())

    def begin_upload(self, data: bytes, *, source: str) -> DocumentRecord:
        self.validate_upload(data)
        record = DocumentRecord(
            id=uuid.uuid4().hex,
            title=read_title(data),
            source=_clean(source, fallback="upload.pdf"),
            year=read_year(data),
            pages=0,
            chunk_count=0,
            index_bytes=0,
            source_bytes=len(data),
            ingested_at=None,
            status="queued",
        )
        with self._lock:
            self._store_new_pdf(record.id, data)
            try:
                self.store.put_record(record)
            except Exception:
                self.store.delete_pdf(record.id)
                raise
        return record

    def accept_upload(self, data: bytes, *, source: str) -> DocumentRecord:
        """Store the PDF, then refuse it when the extracted text would pass the limit.

        File upload and add-by-URL both use this. A refusal deletes the stored
        PDF and the catalog row before the caller returns an error.
        """
        record = self.begin_upload(data, source=source)
        try:
            info = extract_pdf(data)
        except DocumentError:
            self._discard_upload(record.id)
            raise
        with self._lock:
            current = self.store.get_record(record.id)
            if current is None:
                self.store.delete_pdf(record.id)
                raise DocumentError("not_found")
            try:
                self._ensure_capacity(info.index_bytes, excluding=record.id)
            except DocumentError:
                self.store.delete_pdf(record.id)
                self.store.delete_record(record.id)
                raise
            current.index_bytes = info.index_bytes
            self.store.put_record(current)
            return current

    def finish_upload(self, document_id: str, data: bytes) -> None:
        with self._lock:
            record = self.store.get_record(document_id)
            if record is None:
                return
            if not self.store.confirm_pdf(document_id, len(data)):
                record.status = "failed"
                record.error = MESSAGES["store_failed"]
                self.store.put_record(record)
                self._audit("upload_failed", record.id, record.error)
                return
            record.status = "processing"
            record.error = None
            self.store.put_record(record)
            try:
                info = extract_pdf(data)
                self._ensure_capacity(info.index_bytes, excluding=document_id)
                result = self.ingestor.ingest(
                    data,
                    document_id=document_id,
                    title=record.title,
                    source=record.source,
                    year=record.year,
                )
                record.pages = info.pages
                record.chunk_count = result.chunk_count
                record.index_bytes = info.index_bytes
                record.source_bytes = len(data)
                record.ingested_at = utc_now()
                record.status = "ready"
                record.error = None
                self.store.put_record(record)
                self._audit("upload", record.id, record.title)
            except DocumentError as exc:
                record.index_bytes = 0
                record.status = "failed"
                record.error = MESSAGES.get(exc.code, MESSAGES["not_pdf"])
                self.store.put_record(record)
                self._audit("upload_failed", record.id, record.error)
            except Exception:
                logger.exception("upload failed for %s", document_id)
                record.status = "failed"
                record.error = MESSAGES["index_failed"]
                self.store.put_record(record)
                self._audit("upload_failed", record.id, record.error)

    def reset(self) -> None:
        with self._lock:
            self.ingestor.clear()
            self.store.clear_corpus()

    def delete(self, document_id: str) -> None:
        with self._lock:
            record = self.store.get_record(document_id)
            if record is None:
                raise DocumentError("not_found")
            self.ingestor.delete(document_id)
            self.store.delete_pdf(document_id)
            self.store.delete_record(document_id)
            self._audit("delete", document_id, record.title)

    def finish_replace(self, document_id: str, data: bytes) -> None:
        try:
            self.validate_upload(data)
        except DocumentError as exc:
            self._audit("replace_failed", document_id, MESSAGES.get(exc.code, "Replace failed."))
            return
        with self._lock:
            current = self.store.get_record(document_id)
            if current is None:
                return
            try:
                self.store.put_pdf(document_id, data)
                if not self.store.confirm_pdf(document_id, len(data)):
                    raise DocumentError("store_failed")
            except DocumentError as exc:
                self._audit(
                    "replace_failed",
                    document_id,
                    MESSAGES.get(exc.code, MESSAGES["store_failed"]),
                )
                return
            except Exception:
                logger.exception("replace failed for %s", document_id)
                self._audit("replace_failed", document_id, MESSAGES["store_failed"])
                return
            current.status = "processing"
            current.error = None
            current.title = read_title(data)
            current.year = read_year(data)
            current.source_bytes = len(data)
            self.store.put_record(current)
            try:
                info = extract_pdf(data)
                self._ensure_capacity(info.index_bytes, excluding=document_id)
                result = self.ingestor.ingest(
                    data,
                    document_id=document_id,
                    title=current.title,
                    source=current.source,
                    year=current.year,
                )
                current.chunk_count = result.chunk_count
                current.pages = info.pages
                current.index_bytes = info.index_bytes
                current.source_bytes = len(data)
                current.ingested_at = utc_now()
                current.status = "ready"
                current.error = None
                self.store.put_record(current)
                self._audit("replace", current.id, current.title)
            except DocumentError as exc:
                current.status = "failed"
                current.error = MESSAGES.get(exc.code, "Replace failed.")
                self.store.put_record(current)
                self._audit("replace_failed", document_id, current.error)
            except Exception:
                logger.exception("replace failed for %s", document_id)
                current.status = "failed"
                current.error = MESSAGES["index_failed"]
                self.store.put_record(current)
                self._audit("replace_failed", document_id, current.error)

    def _discard_upload(self, document_id: str) -> None:
        with self._lock:
            self.store.delete_pdf(document_id)
            if self.store.get_record(document_id) is not None:
                self.store.delete_record(document_id)

    def _store_new_pdf(self, document_id: str, data: bytes) -> None:
        self.store.put_pdf(document_id, data)
        if self.store.confirm_pdf(document_id, len(data)):
            return
        self.store.delete_pdf(document_id)
        raise DocumentError("store_failed")

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
