from __future__ import annotations

"""Source PDFs, catalog records, and audit events.

Azure Blob is used when an account URL or connection string is set.
Otherwise files live under the local data directory.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Protocol

from docpilot.config import Settings
from docpilot.documents.errors import DocumentError
from docpilot.documents.models import (
    AuditEvent,
    CatalogSummary,
    DocumentRecord,
    audit_from_json,
    record_from_json,
    summary_from_json,
)


class IndexClaim(Protocol):
    def renew(self) -> None: ...

    def release(self) -> None: ...


class DocumentStore(Protocol):
    def put_pdf(self, document_id: str, data: bytes) -> None: ...

    def confirm_pdf(self, document_id: str, size: int) -> bool: ...

    def delete_pdf(self, document_id: str) -> None: ...

    def put_record(self, record: DocumentRecord) -> None: ...

    def get_record(self, document_id: str) -> DocumentRecord | None: ...

    def delete_record(self, document_id: str) -> None: ...

    def list_records(self) -> list[DocumentRecord]: ...

    def put_audit(self, event: AuditEvent) -> None: ...

    def list_audit(self) -> list[AuditEvent]: ...

    def get_summary(self) -> CatalogSummary | None: ...

    def put_summary(self, summary: CatalogSummary) -> None: ...

    def get_pdf(self, document_id: str) -> bytes | None: ...

    def claim_index(self, document_id: str) -> IndexClaim | None: ...

    def clear_corpus(self) -> None: ...


class _MemoryClaim:
    def __init__(self, store: MemoryDocumentStore, document_id: str) -> None:
        self._store = store
        self._document_id = document_id
        self._released = False

    def renew(self) -> None:
        return

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        with self._store._claims_lock:
            self._store._claims.discard(self._document_id)


class MemoryDocumentStore:
    def __init__(self) -> None:
        self.pdfs: dict[str, bytes] = {}
        self.records: dict[str, DocumentRecord] = {}
        self.audit: list[AuditEvent] = []
        self.summary: CatalogSummary | None = None
        self._claims: set[str] = set()
        self._claims_lock = threading.Lock()

    def put_pdf(self, document_id: str, data: bytes) -> None:
        self.pdfs[document_id] = data

    def confirm_pdf(self, document_id: str, size: int) -> bool:
        data = self.pdfs.get(document_id)
        return data is not None and len(data) == size

    def delete_pdf(self, document_id: str) -> None:
        self.pdfs.pop(document_id, None)

    def put_record(self, record: DocumentRecord) -> None:
        self.records[record.id] = record

    def get_record(self, document_id: str) -> DocumentRecord | None:
        return self.records.get(document_id)

    def delete_record(self, document_id: str) -> None:
        self.records.pop(document_id, None)

    def list_records(self) -> list[DocumentRecord]:
        return list(self.records.values())

    def put_audit(self, event: AuditEvent) -> None:
        self.audit.append(event)

    def list_audit(self) -> list[AuditEvent]:
        return list(self.audit)

    def get_summary(self) -> CatalogSummary | None:
        return self.summary

    def put_summary(self, summary: CatalogSummary) -> None:
        self.summary = summary

    def get_pdf(self, document_id: str) -> bytes | None:
        return self.pdfs.get(document_id)

    def claim_index(self, document_id: str) -> IndexClaim | None:
        with self._claims_lock:
            if document_id in self._claims:
                return None
            self._claims.add(document_id)
        return _MemoryClaim(self, document_id)

    def clear_corpus(self) -> None:
        self.pdfs.clear()
        self.records.clear()
        self.audit.clear()
        self.summary = None
        with self._claims_lock:
            self._claims.clear()


class FileDocumentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def put_pdf(self, document_id: str, data: bytes) -> None:
        path = self._path("pdfs", f"{document_id}.pdf")
        path.write_bytes(data)

    def confirm_pdf(self, document_id: str, size: int) -> bool:
        path = self.root / "pdfs" / f"{document_id}.pdf"
        return path.is_file() and path.stat().st_size == size

    def delete_pdf(self, document_id: str) -> None:
        self._path("pdfs", f"{document_id}.pdf").unlink(missing_ok=True)

    def put_record(self, record: DocumentRecord) -> None:
        self._write_json(self._path("catalog", f"{record.id}.json"), record.to_json())

    def get_record(self, document_id: str) -> DocumentRecord | None:
        path = self.root / "catalog" / f"{document_id}.json"
        if not path.is_file():
            return None
        return record_from_json(json.loads(path.read_text(encoding="utf-8")))

    def delete_record(self, document_id: str) -> None:
        (self.root / "catalog" / f"{document_id}.json").unlink(missing_ok=True)

    def list_records(self) -> list[DocumentRecord]:
        records: list[DocumentRecord] = []
        for path in self._json_files("catalog"):
            records.append(record_from_json(json.loads(path.read_text(encoding="utf-8"))))
        return records

    def put_audit(self, event: AuditEvent) -> None:
        self._write_json(self._path("audit", f"{event.id}.json"), event.to_json())

    def list_audit(self) -> list[AuditEvent]:
        events: list[AuditEvent] = []
        for path in self._json_files("audit"):
            events.append(audit_from_json(json.loads(path.read_text(encoding="utf-8"))))
        return events

    def get_summary(self) -> CatalogSummary | None:
        path = self.root / "catalog-summary.json"
        if not path.is_file():
            return None
        return summary_from_json(json.loads(path.read_text(encoding="utf-8")))

    def put_summary(self, summary: CatalogSummary) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_json(self.root / "catalog-summary.json", summary.to_json())

    def get_pdf(self, document_id: str) -> bytes | None:
        path = self.root / "pdfs" / f"{document_id}.pdf"
        if not path.is_file():
            return None
        return path.read_bytes()

    def claim_index(self, document_id: str) -> IndexClaim | None:
        path = self._path("indexing", document_id)
        if path.is_file() and time.time() - path.stat().st_mtime < 90:
            return None
        path.unlink(missing_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        os.close(fd)
        return _FileClaim(path)

    def clear_corpus(self) -> None:
        for folder in ("pdfs", "catalog", "audit", "indexing"):
            directory = self.root / folder
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()
        (self.root / "catalog-summary.json").unlink(missing_ok=True)

    def _write_json(self, path: Path, payload: object) -> None:
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        delay = 0.01
        for attempt in range(8):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.2)

    def _path(self, folder: str, name: str) -> Path:
        directory = self.root / folder
        directory.mkdir(parents=True, exist_ok=True)
        return directory / name

    def _json_files(self, folder: str) -> list[Path]:
        directory = self.root / folder
        if not directory.is_dir():
            return []
        return sorted(directory.glob("*.json"))


class AzureBlobStore:
    """Container `sources`: documents/{id}.pdf, catalog/{id}.json, audit/{id}.json."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._container: object | None = None

    def put_pdf(self, document_id: str, data: bytes) -> None:
        self._upload(f"documents/{document_id}.pdf", data, "application/pdf")

    def confirm_pdf(self, document_id: str, size: int) -> bool:
        from azure.core.exceptions import ResourceNotFoundError

        client = self._client()
        blob = client.get_blob_client(f"documents/{document_id}.pdf")  # type: ignore[attr-defined]
        try:
            props = blob.get_blob_properties()
        except ResourceNotFoundError:
            return False
        return getattr(props, "size", None) == size

    def delete_pdf(self, document_id: str) -> None:
        self._delete(f"documents/{document_id}.pdf")

    def put_record(self, record: DocumentRecord) -> None:
        body = json.dumps(record.to_json()).encode("utf-8")
        self._upload(f"catalog/{record.id}.json", body, "application/json")

    def get_record(self, document_id: str) -> DocumentRecord | None:
        payload = self._download(f"catalog/{document_id}.json")
        if payload is None:
            return None
        return record_from_json(json.loads(payload))

    def delete_record(self, document_id: str) -> None:
        self._delete(f"catalog/{document_id}.json")

    def list_records(self) -> list[DocumentRecord]:
        records: list[DocumentRecord] = []
        for name in self._names("catalog/"):
            payload = self._download(name)
            if payload is not None:
                records.append(record_from_json(json.loads(payload)))
        return records

    def put_audit(self, event: AuditEvent) -> None:
        body = json.dumps(event.to_json()).encode("utf-8")
        self._upload(f"audit/{event.id}.json", body, "application/json")

    def list_audit(self) -> list[AuditEvent]:
        events: list[AuditEvent] = []
        for name in self._names("audit/"):
            payload = self._download(name)
            if payload is not None:
                events.append(audit_from_json(json.loads(payload)))
        return events

    def get_summary(self) -> CatalogSummary | None:
        payload = self._download("catalog-summary.json")
        if payload is None:
            return None
        return summary_from_json(json.loads(payload))

    def put_summary(self, summary: CatalogSummary) -> None:
        body = json.dumps(summary.to_json()).encode("utf-8")
        self._upload("catalog-summary.json", body, "application/json")

    def get_pdf(self, document_id: str) -> bytes | None:
        return self._download(f"documents/{document_id}.pdf")

    def claim_index(self, document_id: str) -> IndexClaim | None:
        from azure.core.exceptions import HttpResponseError, ResourceExistsError

        client = self._client()
        blob = client.get_blob_client(f"indexing/{document_id}")  # type: ignore[attr-defined]
        try:
            blob.upload_blob(b"", overwrite=False)
        except ResourceExistsError:
            pass
        try:
            lease = blob.acquire_lease(lease_duration=60)
        except HttpResponseError:
            return None
        return _AzureClaim(lease)

    def clear_corpus(self) -> None:
        for prefix in ("documents/", "catalog/", "audit/", "indexing/"):
            for name in self._names(prefix):
                self._delete(name)
        self._delete("catalog-summary.json")

    def _client(self) -> object:
        if self._container is not None:
            return self._container
        from azure.core.exceptions import ResourceExistsError
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        if self._settings.storage_connection_string:
            service = BlobServiceClient.from_connection_string(
                self._settings.storage_connection_string
            )
        else:
            service = BlobServiceClient(
                self._settings.storage_account_url,
                credential=DefaultAzureCredential(),
            )
        container = service.get_container_client(self._settings.storage_container)
        try:
            container.create_container()
        except ResourceExistsError:
            pass
        self._container = container
        return container

    def _upload(self, name: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        client = self._client()
        client.upload_blob(  # type: ignore[attr-defined]
            name,
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    def _download(self, name: str) -> bytes | None:
        from azure.core.exceptions import ResourceNotFoundError

        client = self._client()
        try:
            payload = client.download_blob(name).readall()  # type: ignore[attr-defined]
        except ResourceNotFoundError:
            return None
        if not isinstance(payload, bytes):
            raise DocumentError("corrupt")
        return payload

    def _delete(self, name: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        client = self._client()
        try:
            client.delete_blob(name)  # type: ignore[attr-defined]
        except ResourceNotFoundError:
            return

    def _names(self, prefix: str) -> list[str]:
        client = self._client()
        names: list[str] = []
        for blob in client.list_blobs(name_starts_with=prefix):  # type: ignore[attr-defined]
            name = getattr(blob, "name", "")
            if isinstance(name, str):
                names.append(name)
        return sorted(names)


class _FileClaim:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._released = False

    def renew(self) -> None:
        if self._released or not self._path.is_file():
            return
        self._path.touch()

    def release(self) -> None:
        self._released = True
        self._path.unlink(missing_ok=True)


class _AzureClaim:
    def __init__(self, lease: object) -> None:
        self._lease = lease
        self._released = False

    def renew(self) -> None:
        if self._released:
            return
        self._lease.renew()  # type: ignore[attr-defined]

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        from azure.core.exceptions import HttpResponseError

        try:
            self._lease.release()  # type: ignore[attr-defined]
        except HttpResponseError:
            return


def build_store(settings: Settings) -> DocumentStore:
    if settings.storage_connection_string or settings.storage_account_url:
        return AzureBlobStore(settings)
    return FileDocumentStore(settings.data_dir)
