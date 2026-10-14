"""Catalog and audit records stored next to each source PDF."""

from dataclasses import asdict, dataclass
from typing import cast

from docpilot.documents.errors import DocumentError

STATUSES = {"queued", "processing", "ready", "failed"}


@dataclass
class DocumentRecord:
    id: str
    title: str
    source: str
    year: str | None
    pages: int
    chunk_count: int
    index_bytes: int
    source_bytes: int
    ingested_at: str | None
    status: str
    error: str | None = None

    def to_json(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True)
class AuditEvent:
    id: str
    at: str
    actor: str
    action: str
    document_id: str | None
    detail: str | None

    def to_json(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True)
class PdfInfo:
    pages: int
    index_bytes: int


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise DocumentError("corrupt")
    return value


def _optional_str(payload: dict[str, object], key: str) -> str | None:
    if key not in payload or payload[key] is None:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise DocumentError("corrupt")
    return value


def _require_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DocumentError("corrupt")
    return value


def _as_object_dict(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise DocumentError("corrupt")
    result: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise DocumentError("corrupt")
        result[key] = cast(object, value)
    return result


def record_from_json(payload: object) -> DocumentRecord:
    data = _as_object_dict(payload)
    status = _require_str(data, "status")
    if status not in STATUSES:
        raise DocumentError("corrupt")
    return DocumentRecord(
        id=_require_str(data, "id"),
        title=_require_str(data, "title"),
        source=_require_str(data, "source"),
        year=_optional_str(data, "year"),
        pages=_require_int(data, "pages"),
        chunk_count=_require_int(data, "chunk_count"),
        index_bytes=_require_int(data, "index_bytes"),
        source_bytes=_require_int(data, "source_bytes"),
        ingested_at=_optional_str(data, "ingested_at"),
        status=status,
        error=_optional_str(data, "error"),
    )


def audit_from_json(payload: object) -> AuditEvent:
    data = _as_object_dict(payload)
    return AuditEvent(
        id=_require_str(data, "id"),
        at=_require_str(data, "at"),
        actor=_require_str(data, "actor"),
        action=_require_str(data, "action"),
        document_id=_optional_str(data, "document_id"),
        detail=_optional_str(data, "detail"),
    )
