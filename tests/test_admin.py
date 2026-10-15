import os
import re
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pytest import MonkeyPatch, raises

from docpilot.admin.auth import hash_password
from docpilot.config import Settings
from docpilot.documents.errors import DocumentError
from docpilot.documents.models import AuditEvent, DocumentRecord, PdfInfo
from docpilot.documents.service import DocumentService
from docpilot.documents.store import FileDocumentStore, MemoryDocumentStore
from docpilot.ingest.chunk import Chunk
from docpilot.ingest.pipeline import IngestResult
from docpilot.main import create_app

PASSWORD = "correct-horse"


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "admin_password_hash": hash_password(PASSWORD),
        "session_secret": "test-session-secret",
        "cookie_secure": False,
        "storage_account_url": "",
        "storage_container": "sources",
        "storage_connection_string": "",
        "data_dir": tmp_path,
        "max_upload_bytes": 1024 * 1024,
        "index_limit_bytes": 50 * 1024 * 1024,
        "login_max_failures": 5,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _app(tmp_path: Path, store: MemoryDocumentStore | FileDocumentStore | None = None, **overrides: object) -> TestClient:
    application = create_app(_settings(tmp_path, **overrides), store or MemoryDocumentStore())
    return TestClient(application)


def _csrf(page: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)


def _sign_in(client: TestClient, password: str = PASSWORD) -> str:
    login = client.get("/admin/login")
    assert login.status_code == 200
    response = client.post(
        "/admin/login",
        data={"password": password, "csrf": _csrf(login.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/admin")
    assert page.status_code == 200
    return _csrf(page.text)


def test_dotenv_fills_only_missing_names(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "already-set")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("AZURE_STORAGE_CONTAINER", raising=False)
    (tmp_path / ".env").write_text(
        '# local only\nADMIN_PASSWORD_HASH=from-file\nSESSION_SECRET=from-file\nAZURE_STORAGE_CONTAINER="sources"',
        encoding="utf-8",
    )
    settings = Settings.from_env()
    assert settings.admin_password_hash == "already-set"
    assert settings.session_secret == "from-file"
    assert settings.storage_container == "sources"
    os.environ.pop("SESSION_SECRET", None)
    os.environ.pop("AZURE_STORAGE_CONTAINER", None)


def test_health_stays_public(tmp_path: Path) -> None:
    response = _app(tmp_path).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_admin_pages_use_the_logo_as_favicon(tmp_path: Path) -> None:
    client = _app(tmp_path)
    page = client.get("/admin/login")
    assert page.status_code == 200
    assert '<link rel="icon" href="/assets/logo.svg" type="image/svg+xml">' in page.text
    icon = client.get("/assets/logo.svg")
    assert icon.status_code == 200
    assert "svg" in icon.headers["content-type"]
    assert b"<svg" in icon.content


def test_anonymous_admin_is_rejected(tmp_path: Path) -> None:
    client = _app(tmp_path)
    page = client.get("/admin", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/admin/login"
    api = client.get("/admin/api/documents")
    assert api.status_code == 401
    upload = client.post(
        "/admin/api/documents",
        files={"file": ("note.pdf", _pdf(), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert upload.status_code == 401


def test_wrong_password_and_lockout(tmp_path: Path) -> None:
    client = _app(tmp_path, login_max_failures=2)
    login = client.get("/admin/login")
    csrf = _csrf(login.text)
    first = client.post("/admin/login", data={"password": "nope", "csrf": csrf})
    assert first.status_code == 401
    second = client.post("/admin/login", data={"password": "nope", "csrf": csrf})
    assert second.status_code == 429
    locked = client.post("/admin/login", data={"password": PASSWORD, "csrf": csrf})
    assert locked.status_code == 429
    assert "Too many sign-in attempts" in locked.text


def test_upload_replace_and_delete(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    client = _app(tmp_path, store)
    csrf = _sign_in(client)

    missing = client.post(
        "/admin/api/documents",
        data={"csrf": "not-the-token"},
        files={"file": ("note.pdf", _pdf(), "application/pdf")},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert missing.status_code == 403

    uploaded = client.post(
        "/admin/api/documents",
        data={"csrf": csrf, "title": "Visitor guide", "year": "2026"},
        files={"file": ("guide.pdf", _pdf(), "application/pdf")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    catalog = client.get("/admin/api/documents").json()
    assert catalog["documents"][0]["title"] == "Visitor guide"
    assert catalog["documents"][0]["year"] == "2026"
    assert catalog["documents"][0]["status"] == "ready"
    assert catalog["documents"][0]["pages"] == 1
    assert catalog["documents"][0]["chunk_count"] == 0
    document_id = catalog["documents"][0]["id"]
    assert store.pdfs[document_id].startswith(b"%PDF-")

    page = client.get("/admin")
    csrf = _csrf(page.text)
    replaced = client.post(
        f"/admin/api/documents/{document_id}/replace",
        data={"csrf": csrf, "title": "Visitor guide", "year": "2026"},
        files={"file": ("guide.pdf", _pdf(pages=2), "application/pdf")},
        follow_redirects=False,
    )
    assert replaced.status_code == 303
    catalog = client.get("/admin/api/documents").json()
    assert catalog["documents"][0]["pages"] == 2
    assert len(store.pdfs) == 1

    rejected = client.post(
        f"/admin/api/documents/{document_id}/replace",
        data={"csrf": csrf, "title": "Visitor guide", "year": "2026"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
        follow_redirects=True,
    )
    assert rejected.status_code == 200
    assert "not a PDF" in rejected.text
    assert client.get("/admin/api/documents").json()["documents"][0]["pages"] == 2

    page = client.get("/admin")
    csrf = _csrf(page.text)
    deleted = client.post(
        f"/admin/api/documents/{document_id}/delete",
        data={"csrf": csrf},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert client.get("/admin/api/documents").json()["documents"] == []
    assert store.pdfs == {}
    actions = [event.action for event in store.audit]
    assert "upload" in actions
    assert "replace" in actions
    assert "delete" in actions
    assert all("correct-horse" not in (event.detail or "") for event in store.audit)


def test_upload_stops_at_the_index_limit(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    def oversized(_data: bytes) -> PdfInfo:
        return PdfInfo(pages=1, index_bytes=40)

    monkeypatch.setattr("docpilot.documents.service.extract_pdf", oversized)
    store = MemoryDocumentStore()
    client = _app(tmp_path, store, index_limit_bytes=50)
    csrf = _sign_in(client)
    first = client.post(
        "/admin/api/documents",
        data={"csrf": csrf, "title": "First"},
        files={"file": ("a.pdf", _pdf(), "application/pdf")},
        follow_redirects=True,
    )
    assert first.status_code == 200
    csrf = _csrf(first.text)
    client.post(
        "/admin/api/documents",
        data={"csrf": csrf, "title": "Second"},
        files={"file": ("b.pdf", _pdf(), "application/pdf")},
        follow_redirects=True,
    )
    documents = client.get("/admin/api/documents").json()["documents"]
    statuses = {item["title"]: item["status"] for item in documents}
    assert statuses["First"] == "ready"
    assert statuses["Second"] == "failed"
    failed = next(item for item in documents if item["title"] == "Second")
    assert store.pdfs[failed["id"]].startswith(b"%PDF-")


def test_private_urls_are_refused(tmp_path: Path) -> None:
    client = _app(tmp_path)
    csrf = _sign_in(client)
    refused = client.post(
        "/admin/api/documents/url",
        data={"csrf": csrf, "url": "http://127.0.0.1/secret.pdf"},
        headers={"Accept": "application/json"},
    )
    assert refused.status_code == 400
    assert refused.json()["error"] == "bad_url"
    local_file = client.post(
        "/admin/api/documents/url",
        data={"csrf": csrf, "url": "file:///C:/Windows/win.ini"},
        headers={"Accept": "application/json"},
    )
    assert local_file.status_code == 400
    assert local_file.json()["error"] == "bad_url"


def test_file_store_round_trip(tmp_path: Path) -> None:
    store = FileDocumentStore(tmp_path)
    client = _app(tmp_path, store)
    csrf = _sign_in(client)
    client.post(
        "/admin/api/documents",
        data={"csrf": csrf, "title": "On disk"},
        files={"file": ("disk.pdf", _pdf(), "application/pdf")},
    )
    restored = FileDocumentStore(tmp_path)
    records = restored.list_records()
    assert records[0].title == "On disk"
    assert records[0].status == "ready"
    pdf = (tmp_path / "pdfs" / f"{records[0].id}.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-")


def test_unconfigured_admin_asks_the_first_visitor_to_set_it(tmp_path: Path) -> None:
    client = _app(tmp_path, admin_password_hash="", session_secret="")
    page = client.get("/admin/login")
    assert page.status_code == 200
    assert "first person" in page.text
    assert 'action="/admin/setup"' in page.text
    short = client.post(
        "/admin/setup",
        data={"password": "short", "confirm": "short", "csrf": _csrf(page.text)},
    )
    assert short.status_code == 400
    assert "at least 8" in short.text
    mismatch = client.post(
        "/admin/setup",
        data={"password": "first-admin", "confirm": "other-admin", "csrf": _csrf(short.text)},
    )
    assert mismatch.status_code == 400
    saved = client.post(
        "/admin/setup",
        data={"password": "first-admin", "confirm": "first-admin", "csrf": _csrf(mismatch.text)},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin"
    catalog = client.get("/admin")
    assert catalog.status_code == 200
    stored = (tmp_path / "admin-login.json").read_text(encoding="utf-8")
    assert "first-admin" not in stored

    later = TestClient(create_app(_settings(tmp_path, admin_password_hash="", session_secret=""), MemoryDocumentStore()))
    login = later.get("/admin/login")
    assert "first person" not in login.text
    signed = later.post(
        "/admin/login",
        data={"password": "first-admin", "csrf": _csrf(login.text)},
        follow_redirects=False,
    )
    assert signed.status_code == 303


def test_second_visitor_cannot_replace_the_password(tmp_path: Path) -> None:
    first = _app(tmp_path, admin_password_hash="", session_secret="")
    page = first.get("/admin/login")
    first.post(
        "/admin/setup",
        data={"password": "first-admin", "confirm": "first-admin", "csrf": _csrf(page.text)},
        follow_redirects=False,
    )
    second = TestClient(first.app)
    attempt = second.get("/admin/login")
    assert "first person" not in attempt.text
    rejected = second.post(
        "/admin/setup",
        data={"password": "second-admin", "confirm": "second-admin", "csrf": "missing"},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    login = second.get("/admin/login")
    signed = second.post(
        "/admin/login",
        data={"password": "second-admin", "csrf": _csrf(login.text)},
    )
    assert signed.status_code == 401
    owner = second.post(
        "/admin/login",
        data={"password": "first-admin", "csrf": _csrf(signed.text)},
        follow_redirects=False,
    )
    assert owner.status_code == 303


def test_reset_catalog_clears_documents_and_the_audit(tmp_path: Path) -> None:
    store = FileDocumentStore(tmp_path)
    (tmp_path / "admin-login.json").write_text('{"password_hash":"kept"}', encoding="utf-8")
    client = _app(tmp_path, store)
    csrf = _sign_in(client)
    uploaded = client.post(
        "/admin/api/documents",
        data={"csrf": csrf, "title": "Visitor guide", "year": "2026"},
        files={"file": ("guide.pdf", _pdf(), "application/pdf")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    assert store.list_records()
    assert store.list_audit()
    index = client.app.state.documents.ingestor.index
    index.docs["orphan"] = Chunk(
        id="orphan",
        document_id="other",
        title="Old",
        source="old.pdf",
        year=None,
        section="",
        page=1,
        content="stale",
    )
    outsider = TestClient(client.app)
    blocked = outsider.get("/admin/reset", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/admin/login"

    page = client.get("/admin/reset")
    assert page.status_code == 200
    assert "audit log" in page.text
    declined = client.post(
        "/admin/reset",
        data={"csrf": _csrf(page.text), "confirm": "no"},
        follow_redirects=False,
    )
    assert declined.status_code == 303
    assert declined.headers["location"] == "/admin"
    assert store.list_records()
    assert store.list_audit()

    page = client.get("/admin/reset")
    missing = client.post("/admin/reset", data={"csrf": _csrf(page.text)}, follow_redirects=False)
    assert missing.status_code == 303
    assert store.list_records()

    page = client.get("/admin/reset")
    wiped = client.post(
        "/admin/reset",
        data={"csrf": _csrf(page.text), "confirm": "yes"},
        follow_redirects=True,
    )
    assert wiped.status_code == 200
    assert "audit log were deleted" in wiped.text
    assert store.list_records() == []
    assert store.list_audit() == []
    assert list((tmp_path / "pdfs").glob("*.pdf")) == []
    assert index.docs == {}
    assert (tmp_path / "admin-login.json").read_text(encoding="utf-8") == '{"password_hash":"kept"}'


def test_file_store_clear_corpus_leaves_the_admin_login(tmp_path: Path) -> None:
    (tmp_path / "admin-login.json").write_text("{}", encoding="utf-8")
    store = FileDocumentStore(tmp_path)
    store.put_pdf("abc", b"%PDF-1")
    store.put_record(
        DocumentRecord(
            id="abc",
            title="Guide",
            source="guide.pdf",
            year=None,
            pages=1,
            chunk_count=0,
            index_bytes=0,
            source_bytes=6,
            ingested_at=None,
            status="ready",
        )
    )
    store.put_audit(
        AuditEvent(
            id="evt",
            at="2026-10-15T00:00:00+00:00",
            actor="admin",
            action="upload",
            document_id="abc",
            detail="Guide",
        )
    )
    store.clear_corpus()
    assert store.list_records() == []
    assert store.list_audit() == []
    assert not (tmp_path / "pdfs" / "abc.pdf").exists()
    assert (tmp_path / "admin-login.json").read_text(encoding="utf-8") == "{}"


class _RecordingStore(MemoryDocumentStore):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    def put_pdf(self, document_id: str, data: bytes) -> None:
        self.events.append("put_pdf")
        super().put_pdf(document_id, data)

    def confirm_pdf(self, document_id: str, size: int) -> bool:
        self.events.append("confirm_pdf")
        return super().confirm_pdf(document_id, size)

    def delete_pdf(self, document_id: str) -> None:
        self.events.append("delete_pdf")
        super().delete_pdf(document_id)

    def put_record(self, record: DocumentRecord) -> None:
        self.events.append(f"record:{record.status}")
        super().put_record(record)


class _RefuseConfirm(_RecordingStore):
    def confirm_pdf(self, document_id: str, size: int) -> bool:
        self.events.append("confirm_pdf")
        return False


class _BoomIngestor:
    def ingest(self, data: bytes, **kwargs: object) -> IngestResult:
        del data, kwargs
        raise DocumentError("over_capacity")

    def delete(self, document_id: str) -> None:
        del document_id


def test_catalog_is_written_only_after_the_blob_is_confirmed(tmp_path: Path) -> None:
    store = _RecordingStore()
    service = DocumentService(store, _settings(tmp_path))
    data = _pdf()
    seen: dict[str, str] = {}

    def ingest(payload: bytes, **kwargs: object) -> IngestResult:
        store.events.append("ingest")
        document_id = str(kwargs["document_id"])
        assert store.pdfs[document_id] == payload
        saved = store.get_record(document_id)
        assert saved is not None
        assert saved.status == "processing"
        seen["status"] = saved.status
        return IngestResult(pages=1, chunk_count=0, index_bytes=1)

    service.ingestor.ingest = ingest  # type: ignore[method-assign]
    record = service.begin_upload(data, title="Pub 17", source="p17.pdf", year=None)
    assert store.events == ["put_pdf", "confirm_pdf", "record:queued"]
    assert store.pdfs[record.id] == data
    service.finish_upload(record.id, data)
    assert seen["status"] == "processing"
    assert store.events.index("ingest") > store.events.index("record:queued")
    finished = store.get_record(record.id)
    assert finished is not None
    assert finished.status == "ready"


def test_failed_blob_confirmation_leaves_no_catalog_record(tmp_path: Path) -> None:
    store = _RefuseConfirm()
    service = DocumentService(store, _settings(tmp_path))
    with raises(DocumentError) as caught:
        service.begin_upload(_pdf(), title="Pub 17", source="p17.pdf", year=None)
    assert caught.value.code == "store_failed"
    assert store.events == ["put_pdf", "confirm_pdf", "delete_pdf"]
    assert store.list_records() == []
    assert store.pdfs == {}


def test_failed_ingest_keeps_the_stored_pdf(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    service = DocumentService(store, _settings(tmp_path), ingestor=_BoomIngestor())  # type: ignore[arg-type]
    data = _pdf()
    record = service.begin_upload(data, title="Pub 17", source="p17.pdf", year=None)
    service.finish_upload(record.id, data)
    saved = store.get_record(record.id)
    assert saved is not None
    assert saved.status == "failed"
    assert store.pdfs[record.id] == data
