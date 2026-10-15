"""Source PDFs, catalog records, and audit events.

Azure Blob is used when an account URL or connection string is set.
Otherwise files live under the local data directory.
"""

import json
import time
from pathlib import Path
from typing import Protocol

from docpilot.config import Settings
from docpilot.documents.errors import DocumentError
from docpilot.documents.models import AuditEvent, DocumentRecord, audit_from_json, record_from_json


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

    def clear_corpus(self) -> None: ...


class MemoryDocumentStore:
    def __init__(self) -> None:
        self.pdfs: dict[str, bytes] = {}
        self.records: dict[str, DocumentRecord] = {}
        self.audit: list[AuditEvent] = []

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

    def clear_corpus(self) -> None:
        self.pdfs.clear()
        self.records.clear()
        self.audit.clear()


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

    def clear_corpus(self) -> None:
        for folder in ("pdfs", "catalog", "audit"):
            directory = self.root / folder
            if not directory.is_dir():
                continue
            for path in directory.iterdir():
                if path.is_file():
                    path.unlink()

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

    def clear_corpus(self) -> None:
        for prefix in ("documents/", "catalog/", "audit/"):
            for name in self._names(prefix):
                self._delete(name)

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


def build_store(settings: Settings) -> DocumentStore:
    if settings.storage_connection_string or settings.storage_account_url:
        return AzureBlobStore(settings)
    return FileDocumentStore(settings.data_dir)
