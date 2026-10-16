"""Upload, replace, and delete source documents.

A stored PDF is chunked and upserted. When Azure AI Search is not configured,
chunks stay in a process-local index. Old chunk ids are removed only after the
new chunks are upserted. The upload cap uses extracted text size. The usage
meter reads search index storage when the index can report it.
"""

import logging
import threading
import time
import uuid
from datetime import UTC, datetime

from docpilot.config import Settings
from docpilot.documents.describe import read_title, read_year
from docpilot.documents.errors import MESSAGES, DocumentError
from docpilot.documents.models import (
    QUESTION_LIMIT,
    SUMMARY_LIMIT,
    TITLE_LIMIT,
    AuditEvent,
    CatalogSummary,
    DocumentRecord,
    empty_summary,
)
from docpilot.documents.pdf import extract_pdf
from docpilot.documents.store import DocumentStore, IndexClaim
from docpilot.ingest.pipeline import Ingestor, MemoryIndex

logger = logging.getLogger(__name__)


class _RenewedClaim:
    def __init__(self, claim: IndexClaim, document_id: str) -> None:
        self._claim = claim
        self._stop = threading.Event()
        threading.Thread(
            target=self._beat,
            name=f"docpilot-claim-{document_id[:8]}",
            daemon=True,
        ).start()

    def _beat(self) -> None:
        while not self._stop.wait(20):
            try:
                self._claim.renew()
            except Exception:
                logger.exception("could not renew an index claim")

    def release(self) -> None:
        self._stop.set()
        self._claim.release()


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
        self._wake = threading.Event()

    def list_documents(self) -> list[DocumentRecord]:
        return sorted(self.store.list_records(), key=lambda record: record.title.lower())

    def list_audit(self, limit: int = 30) -> list[AuditEvent]:
        events, _total = self.audit_page(limit=limit)
        return events

    def audit_page(self, *, limit: int, offset: int = 0) -> tuple[list[AuditEvent], int]:
        events = sorted(self.store.list_audit(), key=lambda event: event.id, reverse=True)
        start = max(0, offset)
        size = 10 if limit < 1 else min(limit, 1000)
        return events[start : start + size], len(events)

    def wake(self) -> None:
        self._wake.set()

    def get_summary(self) -> CatalogSummary:
        found = self.store.get_summary()
        if found is None:
            return empty_summary()
        return found

    def save_summary(
        self,
        title: str,
        summary: str,
        questions: tuple[str, str, str],
    ) -> CatalogSummary:
        saved = CatalogSummary(
            _bounded(title, TITLE_LIMIT),
            _bounded(summary, SUMMARY_LIMIT),
            (
                _bounded(questions[0], QUESTION_LIMIT),
                _bounded(questions[1], QUESTION_LIMIT),
                _bounded(questions[2], QUESTION_LIMIT),
            ),
        )
        with self._lock:
            self.store.put_summary(saved)
            self._audit("catalog_summary", None, "Catalog summary saved")
        return saved

    def usage_meter(self) -> tuple[int, int, str]:
        """Bytes used, the quota, and the label for the admin meter.

        A search service reports its own index storage and quota. Without one,
        the meter stays on extracted text and the configured limit.
        """
        index = self.ingestor.index
        reader = getattr(index, "storage_usage", None)
        if reader is not None:
            measured = reader()
            if measured is not None:
                used, quota = measured
                return used, quota, "search index"
        return self.index_bytes_used(), self.settings.index_limit_bytes, "extracted text"

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
                self._audit("upload", record.id, _label(record))
            except Exception:
                self.store.delete_pdf(record.id)
                self.store.delete_record(record.id)
                raise
        self.wake()
        return record

    def accept_upload(self, data: bytes, *, source: str) -> DocumentRecord:
        """Store the PDF and catalog row, then return. Indexing runs on the watcher."""
        return self.begin_upload(data, source=source)

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
            if record.status == "queued":
                record.status = "processing"
                record.error = None
                self.store.put_record(record)
        self.index_stored(document_id)

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

    def begin_replace(self, document_id: str, data: bytes) -> None:
        self.validate_upload(data)
        with self._lock:
            current = self.store.get_record(document_id)
            if current is None:
                raise DocumentError("not_found")
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
                raise
            except Exception:
                logger.exception("replace failed for %s", document_id)
                self._audit("replace_failed", document_id, MESSAGES["store_failed"])
                raise DocumentError("store_failed") from None
            current.status = "processing"
            current.error = None
            current.title = read_title(data)
            current.year = read_year(data)
            current.source_bytes = len(data)
            self.store.put_record(current)
        self.wake()

    def incomplete_ids(self) -> list[str]:
        return [
            record.id
            for record in self.store.list_records()
            if record.status in {"queued", "processing"}
        ]

    def discard_unaccounted(self) -> list[str]:
        """Delete stored PDFs and index claims that have no catalog row."""
        removed: list[str] = []
        with self._lock:
            known = {record.id for record in self.store.list_records()}
            for document_id in self.store.list_pdf_ids():
                if document_id in known:
                    continue
                self.store.delete_pdf(document_id)
                removed.append(document_id)
            for document_id in self.store.list_index_ids():
                if document_id in known:
                    continue
                self.store.delete_index_claim(document_id)
                removed.append(document_id)
        return removed

    def watch_incomplete(self, stop: threading.Event, *, pause: float = 15) -> None:
        """Keep trying queued and processing documents until each is ready or failed.

        One document is attempted at a time, oldest upload or retry first.
        A transient error leaves the row processing. The next pass tries it again.
        Another worker holding the claim is skipped until the claim is free.
        """
        not_before: dict[str, float] = {}
        delay_for: dict[str, float] = {}
        while not stop.is_set():
            outcome = "retry"
            ran = False
            try:
                self.discard_unaccounted()
                pending = self._in_order(self.incomplete_ids())
            except Exception:
                logger.exception("could not list documents to index")
                if self._wait(stop, pause):
                    return
                continue
            active = set(pending)
            for document_id in list(not_before):
                if document_id not in active:
                    not_before.pop(document_id, None)
                    delay_for.pop(document_id, None)
            now = time.monotonic()
            for document_id in pending:
                if stop.is_set():
                    return
                if now < not_before.get(document_id, 0):
                    continue
                try:
                    outcome = self._resume_one(document_id)
                except Exception:
                    logger.exception("index attempt failed for %s", document_id)
                    outcome = "retry"
                ran = True
                if outcome == "done":
                    not_before.pop(document_id, None)
                    delay_for.pop(document_id, None)
                elif outcome == "busy":
                    not_before[document_id] = time.monotonic() + pause
                else:
                    delay = delay_for.get(document_id, pause)
                    delay_for[document_id] = min(delay * 2, 300.0)
                    not_before[document_id] = time.monotonic() + delay
                break
            if ran and outcome == "done":
                wait = 0.0
            else:
                upcoming = [
                    moment for moment in not_before.values() if moment > time.monotonic()
                ]
                wait = pause
                if upcoming:
                    wait = min(wait, max(0.0, min(upcoming) - time.monotonic()))
            if self._wait(stop, wait):
                return

    def begin_retry(self, document_id: str) -> None:
        with self._lock:
            record = self.store.get_record(document_id)
            if record is None:
                raise DocumentError("not_found")
            if record.status != "failed":
                raise DocumentError("not_failed")
            if self.store.get_pdf(document_id) is None:
                record.error = MESSAGES["store_failed"]
                self.store.put_record(record)
                self._audit("failed", record.id, record.error)
                raise DocumentError("store_failed")
            record.status = "processing"
            record.error = None
            self.store.put_record(record)
            self._audit("retry", record.id, _label(record))
        self.wake()

    def index_stored(self, document_id: str) -> str:
        record = self.store.get_record(document_id)
        if record is None or record.status not in {"queued", "processing"}:
            return "done"
        data = self.store.get_pdf(document_id)
        if data is None:
            self._mark_failed(document_id, "failed", MESSAGES["store_failed"], zero_index=True)
            return "done"
        with self._lock:
            current = self.store.get_record(document_id)
            if current is None or current.status not in {"queued", "processing"}:
                return "done"
            if current.status == "queued":
                current.status = "processing"
                current.error = None
                self.store.put_record(current)
            record = current
        success, failure, zero_index = _completion(record)
        return self._index_held(
            record,
            data,
            success_action=success,
            failure_action=failure,
            failure_fallback=MESSAGES["not_pdf"] if zero_index else "Replace failed.",
            zero_index=zero_index,
        )

    def _resume_one(self, document_id: str) -> str:
        record = self.store.get_record(document_id)
        if record is None or record.status not in {"queued", "processing"}:
            return "done"
        if self.store.get_pdf(document_id) is None:
            self._mark_failed(document_id, "failed", MESSAGES["store_failed"], zero_index=True)
            return "done"
        return self.index_stored(document_id)

    def _index_held(
        self,
        record: DocumentRecord,
        data: bytes,
        *,
        success_action: str,
        failure_action: str,
        failure_fallback: str,
        zero_index: bool,
    ) -> str:
        claim: _RenewedClaim | None = None
        try:
            claim = self._keep_claim(record.id)
            if claim is None:
                return "busy"
            info = extract_pdf(data)
            with self._lock:
                self._ensure_capacity(info.index_bytes, excluding=record.id)
            result = self.ingestor.ingest(
                data,
                document_id=record.id,
                title=record.title,
                source=record.source,
                year=record.year,
            )
            with self._lock:
                current = self.store.get_record(record.id)
                if current is None:
                    self.ingestor.delete(record.id)
                    return "done"
                current.pages = info.pages
                current.chunk_count = result.chunk_count
                current.index_bytes = info.index_bytes
                current.source_bytes = len(data)
                current.ingested_at = utc_now()
                current.status = "ready"
                current.error = None
                self.store.put_record(current)
                detail = current.title if success_action == "replace" else _label(current)
                self._audit(success_action, current.id, detail)
            return "done"
        except DocumentError as exc:
            message = MESSAGES.get(exc.code, failure_fallback)
            self._mark_failed(record.id, failure_action, message, zero_index=zero_index)
            return "done"
        except Exception:
            logger.exception("index attempt failed for %s", record.id)
            return "retry"
        finally:
            if claim is not None:
                claim.release()

    def _keep_claim(self, document_id: str) -> _RenewedClaim | None:
        claim = self.store.claim_index(document_id)
        if claim is None:
            return None
        return _RenewedClaim(claim, document_id)

    def _mark_failed(
        self,
        document_id: str,
        action: str,
        message: str,
        *,
        zero_index: bool,
    ) -> None:
        with self._lock:
            record = self.store.get_record(document_id)
            if record is None:
                return
            if zero_index:
                record.index_bytes = 0
            record.status = "failed"
            record.error = message
            self.store.put_record(record)
            self._audit(action, record.id, message)

    def _wait(self, stop: threading.Event, seconds: float) -> bool:
        """Return True when the watcher should stop."""
        if stop.is_set():
            return True
        if seconds <= 0:
            return False
        deadline = time.monotonic() + seconds
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self._wake.wait(min(remaining, 0.05)):
                self._wake.clear()
                return stop.is_set()
        return True

    def _in_order(self, document_ids: list[str]) -> list[str]:
        wanted = set(document_ids)
        latest: dict[str, str] = {}
        for event in self.store.list_audit():
            if event.document_id not in wanted or event.action not in {"upload", "retry"}:
                continue
            previous = latest.get(event.document_id)
            if previous is None or event.id > previous:
                latest[event.document_id] = event.id
        return sorted(
            document_ids,
            key=lambda document_id: (latest.get(document_id, "~"), document_id),
        )

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


def _completion(record: DocumentRecord) -> tuple[str, str, bool]:
    if record.ingested_at:
        return "replace", "replace_failed", False
    return "processed", "failed", True


def _label(record: DocumentRecord) -> str:
    return record.title or record.source


def _bounded(value: str, limit: int) -> str:
    text = " ".join(value.split()).strip()
    if len(text) > limit:
        raise DocumentError("summary_long")
    return text


def _clean(value: str, *, fallback: str) -> str:
    text = " ".join(value.split()).strip()
    if not text:
        text = fallback
    return text[:200]
