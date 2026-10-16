import os
import re
import threading
import time
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pytest import MonkeyPatch, raises

from docpilot.admin.auth import hash_password
from docpilot.ask.service import Asker
from docpilot.config import Settings
from docpilot.documents.errors import DocumentError
from docpilot.documents.models import AuditEvent, CatalogSummary, DocumentRecord, PdfInfo
from docpilot.documents.service import DocumentService
from docpilot.documents.store import FileDocumentStore, MemoryDocumentStore
from docpilot.ingest.chunk import Chunk
from docpilot.ingest.pdftext import PageText
from docpilot.ingest.pipeline import Ingestor, IngestResult, MemoryIndex
from docpilot.main import create_app

PASSWORD = "correct-horse"


def _pdf(pages: int = 1, title: str = "") -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if title:
        writer.add_metadata({"/Title": title})
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


def _app(
    tmp_path: Path,
    store: MemoryDocumentStore | FileDocumentStore | None = None,
    asker: Asker | None = None,
    **overrides: object,
) -> TestClient:
    application = create_app(
        _settings(tmp_path, **overrides),
        store or MemoryDocumentStore(),
        asker,
    )
    client = TestClient(application)
    client.__enter__()
    return client


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
    return _csrf(page.text)


def _wait_record(
    store: MemoryDocumentStore | FileDocumentStore,
    document_id: str,
    ready,
    timeout: float = 5,
) -> DocumentRecord:
    deadline = time.monotonic() + timeout
    record = store.get_record(document_id)
    while time.monotonic() < deadline:
        record = store.get_record(document_id)
        if record is not None and ready(record):
            return record
        time.sleep(0.02)
    assert record is not None and ready(record)
    return record


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


def test_admin_pages_credit_the_designer(tmp_path: Path) -> None:
    client = _app(tmp_path)
    login = client.get("/admin/login")
    assert login.status_code == 200
    _sign_in(client)
    pages = (
        login,
        client.get("/admin"),
        client.get("/admin/reset"),
        client.get("/admin/password"),
    )
    for page in pages:
        assert page.status_code == 200
        text = page.text
        assert text.index('class="site-credit"') > text.index("<main>")
        assert "Designed &amp; built by" in text
        assert 'href="https://www.hmeyer.dev" target="_blank" rel="noopener">Henrique Meyer</a>' in text
        assert (
            'href="https://github.com/henrique-meyer/docpilot" target="_blank" rel="noopener"'
            in text
        )
        assert "Source on GitHub" in text
        assert "©" not in text
        assert "Cursor" not in text


def test_admin_pages_use_the_public_header(tmp_path: Path) -> None:
    client = _app(tmp_path)
    login = client.get("/admin/login")
    assert login.status_code == 200
    assert 'background: #0C2340' in login.text
    assert '<img src="/assets/logo.svg" alt="">' in login.text
    assert '<a href="/">DocPilot Admin</a>' in login.text
    assert "<h1>Login</h1>" in login.text
    assert "Manage source documents." in login.text
    assert "<summary>Admin</summary>" not in login.text
    _sign_in(client)
    documents = client.get("/admin")
    reset = client.get("/admin/reset")
    password = client.get("/admin/password")
    for page in (documents, reset, password):
        assert page.status_code == 200
        assert '<img src="/assets/logo.svg" alt="">' in page.text
        assert '<a href="/">DocPilot Admin</a>' in page.text
        assert "<summary>Admin</summary>" in page.text
        assert '<a href="/admin">Documents</a>' in page.text
        assert '<a href="/admin/reset">Reset Catalog</a>' in page.text
        assert '<a href="/admin/password">Change Password</a>' in page.text
        assert ">Sign Out</button>" in page.text
    assert 'href="/admin/reset">Reset catalog' not in documents.text
    assert ">Sign out</button>" not in documents.text


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
    documents = client.get("/admin")
    upload_form = documents.text.split("<h2>Catalog Summary</h2>", 1)[0]
    assert 'name="title"' not in upload_form
    assert 'name="year"' not in documents.text
    assert "Add by URL" not in documents.text
    assert 'name="url"' not in documents.text
    assert ">Add URL</button>" not in documents.text
    assert 'action="/admin/api/documents/url"' not in documents.text
    assert "Upload a PDF" in documents.text
    assert 'type="file"' in documents.text
    assert 'name="file"' in documents.text
    assert 'class="dropzone"' in documents.text
    assert ">Upload</button>" not in documents.text

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
        data={"csrf": csrf},
        files={"file": ("guide.pdf", _pdf(title="2026 Visitor guide"), "application/pdf")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    stored = store.list_records()[0]
    assert stored.title == "2026 Visitor guide"
    assert stored.year == "2026"
    ready = _wait_record(store, stored.id, lambda item: item.status == "ready")
    catalog = client.get("/admin/api/documents").json()
    assert catalog["documents"][0]["title"] == "2026 Visitor guide"
    assert catalog["documents"][0]["year"] == "2026"
    assert catalog["documents"][0]["status"] == "ready"
    assert catalog["documents"][0]["pages"] == 1
    assert catalog["documents"][0]["chunk_count"] == 0
    document_id = ready.id
    assert store.pdfs[document_id].startswith(b"%PDF-")

    page = client.get("/admin")
    csrf = _csrf(page.text)
    replaced = client.post(
        f"/admin/api/documents/{document_id}/replace",
        data={"csrf": csrf},
        files={"file": ("guide.pdf", _pdf(pages=2, title="2024 Replacement"), "application/pdf")},
        follow_redirects=False,
    )
    assert replaced.status_code == 303
    _wait_record(
        store,
        document_id,
        lambda item: item.status == "ready" and item.pages == 2,
    )
    catalog = client.get("/admin/api/documents").json()
    assert catalog["documents"][0]["title"] == "2024 Replacement"
    assert catalog["documents"][0]["year"] == "2024"
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


def test_admin_messages_stay_out_of_the_address_bar(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    client = _app(tmp_path, store)
    csrf = _sign_in(client)
    uploaded = client.post(
        "/admin/api/documents",
        data={"csrf": csrf, "title": "Visitor guide", "year": "2026"},
        files={"file": ("guide.pdf", _pdf(), "application/pdf")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    assert uploaded.headers["location"] == "/admin"
    cookie = uploaded.headers["set-cookie"].lower()
    assert "docpilot_flash" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    record = store.list_records()[0]
    record.status = "processing"
    polling = client.get("/admin")
    assert "The file is stored" in polling.text
    assert 'data-autodismiss' in polling.text
    assert polling.url.path == "/admin"
    assert not polling.url.query
    still = client.get("/admin")
    assert "The file is stored" not in still.text
    record.status = "ready"
    quiet = client.get("/admin")
    assert "The file is stored" not in quiet.text

    stale = client.get("/admin?notice=deleted&error=not_pdf")
    assert "Document deleted." not in stale.text
    assert "not a PDF" not in stale.text

    rejected = client.post(
        "/admin/api/documents",
        data={"csrf": _csrf(quiet.text)},
        files={"file": ("notes.txt", b"hello", "text/plain")},
        follow_redirects=False,
    )
    assert rejected.headers["location"] == "/admin"
    failed = client.get("/admin")
    assert "not a PDF" in failed.text
    assert failed.url.path == "/admin"
    assert not failed.url.query
    assert "not a PDF" not in client.get("/admin").text

    page = client.get("/admin")
    document_id = store.list_records()[0].id
    deleted = client.post(
        f"/admin/api/documents/{document_id}/delete",
        data={"csrf": _csrf(page.text)},
        follow_redirects=False,
    )
    assert deleted.headers["location"] == "/admin"
    gone = client.get("/admin")
    assert "Document deleted." in gone.text
    assert "Document deleted." not in client.get("/admin").text

    signed_out = client.post(
        "/admin/logout",
        data={"csrf": _csrf(gone.text)},
        follow_redirects=False,
    )
    assert signed_out.headers["location"] == "/admin/login"
    login = client.get("/admin/login")
    assert "Signed out." in login.text
    assert login.url.path == "/admin/login"
    assert not login.url.query
    assert "Signed out." not in client.get("/admin/login").text

    api = client.post(
        "/admin/login",
        data={"password": PASSWORD, "csrf": _csrf(client.get("/admin/login").text)},
        follow_redirects=False,
    )
    assert api.status_code == 303
    csrf = _csrf(client.get("/admin").text)
    queued = client.post(
        "/admin/api/documents",
        data={"csrf": csrf, "title": "API"},
        files={"file": ("api.pdf", _pdf(), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert queued.status_code == 202
    assert queued.json()["message"] == "The file is stored. Indexing continues until the status is ready."
    assert "location" not in queued.headers
    assert "docpilot_flash" not in queued.headers.get("set-cookie", "")


def test_admin_forms_answer_json_on_the_same_page(tmp_path: Path) -> None:
    client = _app(tmp_path)
    login = client.get("/admin/login")
    assert 'id="page-banner"' in login.text
    assert "preventDefault" in login.text
    bad = client.post(
        "/admin/login",
        data={"password": "nope", "csrf": _csrf(login.text)},
        headers={"Accept": "application/json"},
    )
    assert bad.status_code == 401
    assert bad.json()["message"] == "The password is wrong."
    assert "location" not in bad.headers

    csrf = _sign_in(client)
    documents = client.get("/admin")
    assert 'id="upload-progress"' in documents.text
    assert "File received. Still working" in documents.text
    assert "XMLHttpRequest" in documents.text
    assert 'http-equiv="refresh"' not in documents.text
    assert 'id="catalog-panel"' in documents.text
    uploaded = client.post(
        "/admin/api/documents",
        data={"csrf": csrf},
        files={"file": ("guide.pdf", _pdf(title="Async guide"), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert uploaded.status_code == 202
    document_id = uploaded.json()["id"]
    deleted = client.post(
        f"/admin/api/documents/{document_id}/delete",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["message"] == "Document deleted."
    assert "location" not in deleted.headers
    short = client.post(
        "/admin/password",
        data={"csrf": csrf, "password": "short", "confirm": "short"},
        headers={"Accept": "application/json"},
    )
    assert short.status_code == 400
    assert short.json()["message"] == "Use at least 8 characters."
    signed_out = client.post(
        "/admin/logout",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert signed_out.status_code == 200
    assert signed_out.json()["next"] == "/admin/login"
    assert signed_out.json()["message"] == "Signed out."
    assert client.get("/admin", follow_redirects=False).status_code == 303


def test_password_change_rejects_a_short_password_and_a_mismatch(tmp_path: Path) -> None:
    client = _app(tmp_path)
    csrf = _sign_in(client)
    page = client.get("/admin/password")
    assert page.status_code == 200
    assert '<a class="quiet" href="/admin">Cancel</a>' in page.text
    assert 'href="/admin/password"' in client.get("/admin").text
    short = client.post(
        "/admin/password",
        data={"csrf": csrf, "password": "short", "confirm": "short"},
    )
    assert short.status_code == 400
    assert "at least 8 characters" in short.text
    mismatch = client.post(
        "/admin/password",
        data={"csrf": _csrf(short.text), "password": "new-password", "confirm": "other-password"},
    )
    assert mismatch.status_code == 400
    assert "do not match" in mismatch.text
    outsider = TestClient(client.app)
    still = outsider.post(
        "/admin/login",
        data={"password": PASSWORD, "csrf": _csrf(outsider.get("/admin/login").text)},
        follow_redirects=False,
    )
    assert still.status_code == 303


def test_password_change_keeps_the_session_and_replaces_the_hash(tmp_path: Path) -> None:
    client = _app(tmp_path)
    csrf = _sign_in(client)
    changed = client.post(
        "/admin/password",
        data={"csrf": csrf, "password": "new-password", "confirm": "new-password"},
        follow_redirects=False,
    )
    assert changed.status_code == 303
    assert changed.headers["location"] == "/admin"
    landed = client.get("/admin")
    assert landed.status_code == 200
    assert "Password updated." in landed.text
    stored = (tmp_path / "admin-login.json").read_text(encoding="utf-8")
    assert "new-password" not in stored
    assert "test-session-secret" in stored
    outsider = TestClient(client.app)
    login = outsider.get("/admin/login")
    old = outsider.post(
        "/admin/login",
        data={"password": PASSWORD, "csrf": _csrf(login.text)},
        follow_redirects=False,
    )
    assert old.status_code == 401
    new = outsider.post(
        "/admin/login",
        data={"password": "new-password", "csrf": _csrf(old.text)},
        follow_redirects=False,
    )
    assert new.status_code == 303


def test_anonymous_password_page_goes_to_sign_in(tmp_path: Path) -> None:
    client = _app(tmp_path)
    page = client.get("/admin/password", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/admin/login"


def test_upload_stops_at_the_index_limit(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    def oversized(_data: bytes) -> PdfInfo:
        return PdfInfo(pages=1, index_bytes=40)

    monkeypatch.setattr("docpilot.documents.service.extract_pdf", oversized)
    store = MemoryDocumentStore()
    client = _app(tmp_path, store, index_limit_bytes=50)
    csrf = _sign_in(client)
    first = client.post(
        "/admin/api/documents",
        data={"csrf": csrf},
        files={"file": ("a.pdf", _pdf(title="First"), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert first.status_code == 202
    ready = _wait_record(store, first.json()["id"], lambda item: item.status == "ready")
    assert ready.title == "First"
    assert ready.index_bytes == 40
    second = client.post(
        "/admin/api/documents",
        data={"csrf": csrf},
        files={"file": ("b.pdf", _pdf(title="Second"), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert second.status_code == 202
    failed = _wait_record(store, second.json()["id"], lambda item: item.status == "failed")
    assert failed.error == "That upload would pass the 50 MB index limit."
    assert store.pdfs[failed.id].startswith(b"%PDF-")
    catalog = client.get("/admin/api/catalog")
    assert "Second" in catalog.text
    assert f'/admin/api/documents/{failed.id}/retry' in catalog.text
    assert f'/admin/api/documents/{failed.id}/delete' in catalog.text
    third = client.post(
        "/admin/api/documents",
        data={"csrf": csrf},
        files={"file": ("c.pdf", _pdf(title="Third"), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert third.status_code == 202
    third_row = _wait_record(store, third.json()["id"], lambda item: item.status == "failed")
    assert third_row.error == "That upload would pass the 50 MB index limit."
    assert len(store.list_records()) == 3
    removed = client.post(
        f"/admin/api/documents/{third_row.id}/delete",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert removed.status_code == 200
    assert store.get_record(third_row.id) is None
    assert third_row.id not in store.pdfs

    def url_pdf(_url: str, _limit: int) -> bytes:
        return _pdf(title="From URL")

    monkeypatch.setattr("docpilot.admin.routes.download_pdf", url_pdf)
    by_url = client.post(
        "/admin/api/documents/url",
        data={"csrf": csrf, "url": "https://example.gov/guide.pdf"},
        headers={"Accept": "application/json"},
    )
    assert by_url.status_code == 202
    from_url = _wait_record(store, by_url.json()["id"], lambda item: item.status == "failed")
    assert from_url.error == "That upload would pass the 50 MB index limit."
    assert store.get_pdf(from_url.id) is not None


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
        data={"csrf": csrf},
        files={"file": ("disk.pdf", _pdf(title="On disk"), "application/pdf")},
    )
    stored = store.list_records()[0]
    _wait_record(store, stored.id, lambda item: item.status == "ready")
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
    client.app.state.documents.save_summary("Kept title", "Kept summary", ("Q1", "Q2", "Q3"))
    assert store.get_summary() is not None
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
    assert store.get_summary() is not None

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
    assert store.get_summary() is None
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
    store.put_summary(CatalogSummary("T", "S", ("a", "b", "c")))
    store.clear_corpus()
    assert store.list_records() == []
    assert store.list_audit() == []
    assert store.get_summary() is None
    assert not (tmp_path / "catalog-summary.json").exists()
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


class _FlakyIngestor:
    def __init__(self) -> None:
        self.calls = 0
        self._inner = Ingestor(None, MemoryIndex())

    def ingest(self, data: bytes, **kwargs: object) -> IngestResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary")
        return self._inner.ingest(data, **kwargs)

    def delete(self, document_id: str) -> None:
        self._inner.delete(document_id)

    def clear(self) -> None:
        self._inner.clear()


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
        assert [event.action for event in store.audit] == ["upload"]
        seen["status"] = saved.status
        return IngestResult(pages=1, chunk_count=0, index_bytes=1)

    service.ingestor.ingest = ingest  # type: ignore[method-assign]
    record = service.begin_upload(data, source="p17.pdf")
    assert store.events == ["put_pdf", "confirm_pdf", "record:queued"]
    assert store.pdfs[record.id] == data
    service.finish_upload(record.id, data)
    assert seen["status"] == "processing"
    assert store.events.index("ingest") > store.events.index("record:queued")
    finished = store.get_record(record.id)
    assert finished is not None
    assert finished.status == "ready"
    assert [event.action for event in store.audit] == ["upload", "processed"]


def test_failed_blob_confirmation_leaves_no_catalog_record(tmp_path: Path) -> None:
    store = _RefuseConfirm()
    service = DocumentService(store, _settings(tmp_path))
    with raises(DocumentError) as caught:
        service.begin_upload(_pdf(), source="p17.pdf")
    assert caught.value.code == "store_failed"
    assert store.events == ["put_pdf", "confirm_pdf", "delete_pdf"]
    assert store.list_records() == []
    assert store.pdfs == {}


def test_failed_ingest_keeps_the_stored_pdf(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    service = DocumentService(store, _settings(tmp_path), ingestor=_BoomIngestor())  # type: ignore[arg-type]
    data = _pdf()
    record = service.begin_upload(data, source="p17.pdf")
    service.finish_upload(record.id, data)
    saved = store.get_record(record.id)
    assert saved is not None
    assert saved.status == "failed"
    assert store.pdfs[record.id] == data
    assert [event.action for event in store.audit] == ["upload", "failed"]


def test_catalog_table_fills_its_panel(tmp_path: Path) -> None:
    client = _app(tmp_path, MemoryDocumentStore())
    _sign_in(client)
    page = client.get("/admin")
    assert "#catalog-panel table { width: 100%; }" in page.text


def test_shown_timestamps_are_marked_for_the_browser_timezone(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    store.put_record(
        DocumentRecord(
            id="ready",
            title="Ready guide",
            source="ready.pdf",
            year=None,
            pages=1,
            chunk_count=1,
            index_bytes=10,
            source_bytes=10,
            ingested_at="2026-10-15T12:08:07+00:00",
            status="ready",
        )
    )
    store.put_record(
        DocumentRecord(
            id="working",
            title="Working guide",
            source="working.pdf",
            year=None,
            pages=0,
            chunk_count=0,
            index_bytes=10,
            source_bytes=10,
            ingested_at=None,
            status="processing",
        )
    )
    store.put_audit(
        AuditEvent(
            id="evt",
            at="2026-10-15T12:08:07+00:00",
            actor="admin",
            action="processed",
            document_id="ready",
            detail="Ready guide",
        )
    )
    client = _app(tmp_path, store)
    _sign_in(client)
    page = client.get("/admin")
    catalog = client.get("/admin/api/catalog")
    audit = client.get("/admin/api/audit")
    stamp = "2026-10-15T12:08:07+00:00"
    marked = f'<time class="local-time" datetime="{stamp}">{stamp}</time>'
    assert marked in catalog.text
    assert marked in audit.text
    assert catalog.text.count("local-time") == 1
    assert ">—<" in catalog.text
    assert "function formatLocalTimes" in page.text
    assert "toLocaleString(undefined" in page.text
    assert "timeZoneName" in page.text


def test_catalog_audit_and_usage_load_on_their_own(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    client = _app(tmp_path, store)
    csrf = _sign_in(client)
    uploaded = client.post(
        "/admin/api/documents",
        data={"csrf": csrf},
        files={"file": ("guide.pdf", _pdf(title="2026 Visitor guide"), "application/pdf")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    stored = store.list_records()[0]
    _wait_record(store, stored.id, lambda item: item.status == "ready")
    page = client.get("/admin")
    assert 'id="usage-panel"' in page.text
    assert 'id="catalog-panel"' in page.text
    assert 'id="audit-panel"' in page.text
    assert "Loading…" in page.text
    assert "No documents yet." not in page.text
    assert "No admin actions yet." not in page.text
    assert "Source files stored:" not in page.text
    assert "2026 Visitor guide" not in page.text
    assert "DOMParser" not in page.text
    assert "/admin/api/usage" in page.text
    assert "/admin/api/catalog" in page.text
    assert "/admin/api/audit" in page.text
    assert "2000" in page.text
    assert "30000" in page.text
    assert ".status-queued, #catalog-panel .status-processing" in page.text

    usage = client.get("/admin/api/usage")
    catalog = client.get("/admin/api/catalog")
    audit = client.get("/admin/api/audit")
    assert usage.status_code == 200
    assert "extracted text" in usage.text
    assert "Source files stored:" in usage.text
    assert 'aria-label="Extracted text toward the index limit"' in usage.text
    assert catalog.status_code == 200
    assert "2026 Visitor guide" in catalog.text
    assert "status-ready" in catalog.text
    assert 'name="csrf"' in catalog.text
    assert audit.status_code == 200
    assert ">upload<" in audit.text
    assert ">processed<" in audit.text
    assert "2026 Visitor guide" in audit.text
    assert "Replace" not in catalog.text
    assert "<th>Year</th>" not in catalog.text
    assert "nowrap" in catalog.text
    assert 'aria-label="Delete"' in catalog.text
    assert 'id="delete-confirm"' in page.text
    assert ">Yes<" in page.text
    assert ">No<" in page.text

    record = store.list_records()[0]
    record.status = "processing"
    processing = client.get("/admin/api/catalog")
    assert "status-processing" in processing.text
    quiet = client.get("/admin/api/usage")
    assert "Source files stored:" in quiet.text

    outsider = TestClient(client.app)
    assert outsider.get("/admin/api/usage").status_code == 401
    assert outsider.get("/admin/api/catalog").status_code == 401
    assert outsider.get("/admin/api/audit").status_code == 401


class _DraftChat:
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> tuple[str, int]:
        self.prompts.append(prompt)
        return self.text, 9


def _ready_document(store: MemoryDocumentStore) -> None:
    store.put_record(
        DocumentRecord(
            id="doc1",
            title="2025 Publication 17",
            source="p17.pdf",
            year="2025",
            pages=1,
            chunk_count=1,
            index_bytes=20,
            source_bytes=20,
            ingested_at="2026-10-15T00:00:00+00:00",
            status="ready",
        )
    )
    store.put_pdf("doc1", _pdf(title="2025 Publication 17"))


def test_catalog_summary_saves_without_leaving_the_page(tmp_path: Path) -> None:
    client = _app(tmp_path)
    csrf = _sign_in(client)
    page = client.get("/admin")
    assert "<h2>Catalog Summary</h2>" in page.text
    assert page.text.index("catalog-summary-form") > page.text.index('id="catalog-panel"')
    assert 'id="catalog-summary-draft"' in page.text
    assert "Auto-complete with AI" in page.text
    assert "Sample questions" in page.text
    assert 'name="question_1"' in page.text
    assert 'name="question_2"' in page.text
    assert 'name="question_3"' in page.text
    assert 'id="catalog-summary-save" disabled' in page.text
    assert "/admin/api/catalog-summary/draft" in page.text
    assert "refreshSummarySave" in page.text
    catalog = client.get("/admin/api/catalog")
    assert "catalog-summary-form" not in catalog.text

    saved = client.post(
        "/admin/api/catalog-summary",
        data={
            "csrf": csrf,
            "title": "  IRS Publications for use in preparing 2025 returns  ",
            "summary": "These documents provide guidance for filing 2025 personal tax returns.",
            "question_1": "Do dead people have to file taxes?",
            "question_2": "Is car loan interest taxed?",
            "question_3": "What if my wife doesn't live in the US?",
        },
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["message"] == "Catalog summary saved."
    assert body["title"] == "IRS Publications for use in preparing 2025 returns"
    assert "next" not in body
    assert "location" not in saved.headers
    again = client.get("/admin")
    assert "IRS Publications for use in preparing 2025 returns" in again.text
    assert "Do dead people have to file taxes?" in again.text
    assert 'id="catalog-summary-save" disabled' in again.text
    audit = client.get("/admin/api/audit")
    assert ">catalog_summary<" in audit.text
    assert "Catalog summary saved" in audit.text
    assert "These documents provide guidance" not in audit.text

    outsider = TestClient(client.app)
    denied = outsider.post(
        "/admin/api/catalog-summary",
        data={"title": "Nope"},
        headers={"Accept": "application/json"},
    )
    assert denied.status_code == 401
    forged = client.post(
        "/admin/api/catalog-summary",
        data={"csrf": "nope", "title": "Nope"},
        headers={"Accept": "application/json"},
    )
    assert forged.status_code == 403
    too_long = client.post(
        "/admin/api/catalog-summary",
        data={"csrf": csrf, "title": "x" * 201, "summary": "", "question_1": "", "question_2": "", "question_3": ""},
        headers={"Accept": "application/json"},
    )
    assert too_long.status_code == 400
    assert too_long.json()["error"] == "summary_long"
    kept = client.app.state.documents.get_summary()
    assert kept.title == "IRS Publications for use in preparing 2025 returns"


def test_catalog_summary_draft_fills_the_form_and_does_not_save(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "docpilot.documents.summary.read_pages",
        lambda data: [PageText(1, "", "Deceased taxpayers may still need to file a return.")],
    )
    store = MemoryDocumentStore()
    _ready_document(store)
    chat = _DraftChat(
        '{"title":"IRS Publications for use in preparing 2025 returns",'
        '"summary":"These documents provide guidance for filing 2025 personal tax returns.",'
        '"questions":["Do dead people have to file taxes?","Is car loan interest taxed?",'
        '"What if my wife does not live in the US?"]}'
    )
    client = _app(tmp_path, store, Asker(None, chat))
    csrf = _sign_in(client)
    drafted = client.post(
        "/admin/api/catalog-summary/draft",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert drafted.status_code == 200
    body = drafted.json()
    assert body["title"] == "IRS Publications for use in preparing 2025 returns"
    assert body["questions"][0] == "Do dead people have to file taxes?"
    assert "message" not in body
    assert store.get_summary() is None
    assert chat.prompts
    assert "Deceased taxpayers may still need to file a return." in chat.prompts[0]
    assert "2025 Publication 17" in chat.prompts[0]
    assert not any(event.action == "catalog_summary" for event in store.list_audit())

    outsider = TestClient(client.app)
    assert outsider.post(
        "/admin/api/catalog-summary/draft",
        headers={"Accept": "application/json"},
    ).status_code == 401


def test_catalog_summary_draft_refuses_an_empty_catalog(tmp_path: Path) -> None:
    chat = _DraftChat("{}")
    client = _app(tmp_path, asker=Asker(None, chat))
    csrf = _sign_in(client)
    drafted = client.post(
        "/admin/api/catalog-summary/draft",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert drafted.status_code == 400
    assert drafted.json()["error"] == "no_documents"
    assert chat.prompts == []


def test_catalog_summary_draft_needs_the_chat_model(tmp_path: Path) -> None:
    client = _app(tmp_path)
    csrf = _sign_in(client)
    drafted = client.post(
        "/admin/api/catalog-summary/draft",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert drafted.status_code == 503
    assert drafted.json()["message"] == "The chat model is not configured."


def test_catalog_summary_is_stored_beside_the_catalog(tmp_path: Path) -> None:
    store = FileDocumentStore(tmp_path)
    service = DocumentService(store, _settings(tmp_path))
    service.save_summary("Shared title", "Shared summary", ("One?", "Two?", "Three?"))
    assert (tmp_path / "catalog-summary.json").is_file()
    assert list((tmp_path / "catalog").glob("*.json")) == []
    restored = FileDocumentStore(tmp_path)
    found = restored.get_summary()
    assert found is not None
    assert found.summary == "Shared summary"
    assert found.questions == ("One?", "Two?", "Three?")
    assert restored.list_audit()[0].detail == "Catalog summary saved"
    restored.clear_corpus()
    assert restored.get_summary() is None


def _stored(
    document_id: str,
    status: str,
    *,
    title: str = "Guide",
    source: str = "guide.pdf",
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        title=title,
        source=source,
        year=None,
        pages=0,
        chunk_count=0,
        index_bytes=10,
        source_bytes=20,
        ingested_at=None,
        status=status,
    )


def test_startup_resumes_documents_left_queued_or_processing(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    data = _pdf(title="2024 Stuck guide")
    store.put_pdf("stuck", data)
    store.put_record(_stored("stuck", "processing", title="2024 Stuck guide", source="stuck.pdf"))
    store.put_record(_stored("missing", "queued", title="Missing guide", source="missing.pdf"))
    store.put_record(
        DocumentRecord(
            id="ready",
            title="Ready guide",
            source="ready.pdf",
            year=None,
            pages=2,
            chunk_count=3,
            index_bytes=30,
            source_bytes=40,
            ingested_at="2026-10-15T12:00:00+00:00",
            status="ready",
        )
    )
    stuck = store.get_record("stuck")
    missing = store.get_record("missing")
    with TestClient(create_app(_settings(tmp_path), store)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            stuck = store.get_record("stuck")
            missing = store.get_record("missing")
            if (
                stuck is not None
                and stuck.status == "ready"
                and missing is not None
                and missing.status == "failed"
            ):
                break
            time.sleep(0.02)
    assert stuck is not None
    assert stuck.status == "ready"
    assert stuck.pages == 1
    assert stuck.title == "2024 Stuck guide"
    assert missing is not None
    assert missing.status == "failed"
    assert missing.error == "The file could not be stored."
    ready = store.get_record("ready")
    assert ready is not None
    assert ready.status == "ready"
    assert ready.pages == 2
    actions = [event.action for event in store.audit]
    assert actions == ["processed", "failed"] or actions == ["failed", "processed"]
    assert "upload" not in actions


def test_transient_index_failure_is_retried_until_ready(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    flaky = _FlakyIngestor()
    service = DocumentService(store, _settings(tmp_path), ingestor=flaky)  # type: ignore[arg-type]
    data = _pdf(title="2024 Flaky guide")
    store.put_pdf("flaky", data)
    store.put_record(_stored("flaky", "processing", title="2024 Flaky guide", source="flaky.pdf"))
    stop = threading.Event()
    thread = threading.Thread(
        target=service.watch_incomplete,
        args=(stop,),
        kwargs={"pause": 0.01},
    )
    thread.start()
    saved = store.get_record("flaky")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        saved = store.get_record("flaky")
        if saved is not None and saved.status == "ready":
            break
        time.sleep(0.02)
    stop.set()
    thread.join(timeout=2)
    assert saved is not None
    assert saved.status == "ready"
    assert saved.error is None
    assert flaky.calls >= 2


def test_failed_document_can_be_retried_from_the_catalog(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    service = DocumentService(store, _settings(tmp_path), ingestor=_BoomIngestor())  # type: ignore[arg-type]
    data = _pdf(title="2024 Failed guide")
    failed = service.begin_upload(data, source="failed.pdf")
    service.finish_upload(failed.id, data)
    saved = store.get_record(failed.id)
    assert saved is not None
    assert saved.status == "failed"
    assert store.pdfs[failed.id] == data
    store.put_record(_stored("ready-doc", "ready", title="Ready guide", source="ready.pdf"))
    client = _app(tmp_path, store)
    csrf = _sign_in(client)
    catalog = client.get("/admin/api/catalog")
    assert catalog.text.count(">Retry<") == 1
    assert f'/admin/api/documents/{failed.id}/retry' in catalog.text
    assert "/admin/api/documents/ready-doc/retry" not in catalog.text
    outsider = TestClient(client.app)
    assert outsider.post(
        f"/admin/api/documents/{failed.id}/retry",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    ).status_code == 401
    blocked = client.post(
        "/admin/api/documents/ready-doc/retry",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["message"] == "Only a failed document can be retried."
    retried = client.post(
        f"/admin/api/documents/{failed.id}/retry",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert retried.status_code == 202
    assert retried.json()["message"] == "Indexing started again."
    done = _wait_record(store, failed.id, lambda item: item.status == "ready")
    assert done.status == "ready"
    assert store.pdfs[failed.id] == data
    actions = [event.action for event in store.audit]
    assert "retry" in actions
    assert "processed" in actions
    assert ">Retry<" not in client.get("/admin/api/catalog").text


def test_two_uploads_return_before_ingest_finishes(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    client = _app(tmp_path, store)
    csrf = _sign_in(client)
    gate = threading.Event()
    started: list[str] = []
    original = client.app.state.documents.ingestor.ingest

    def ingest(data: bytes, **kwargs: object) -> IngestResult:
        started.append(str(kwargs["document_id"]))
        assert gate.wait(5)
        return original(data, **kwargs)

    client.app.state.documents.ingestor.ingest = ingest  # type: ignore[method-assign]
    first = client.post(
        "/admin/api/documents",
        data={"csrf": csrf},
        files={"file": ("a.pdf", _pdf(title="First guide"), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert first.status_code == 202
    deadline = time.monotonic() + 5
    while not started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert started
    began = time.monotonic()
    second = client.post(
        "/admin/api/documents",
        data={"csrf": csrf},
        files={"file": ("b.pdf", _pdf(title="Second guide"), "application/pdf")},
        headers={"Accept": "application/json"},
    )
    assert second.status_code == 202
    assert time.monotonic() - began < 1
    assert "location" not in second.headers
    records = {record.id: record for record in store.list_records()}
    assert set(records) == {first.json()["id"], second.json()["id"]}
    assert all(record.status in {"queued", "processing"} for record in records.values())
    assert [event.action for event in store.list_audit()].count("upload") == 2
    catalog = client.get("/admin/api/catalog")
    assert "First guide" in catalog.text
    assert "Second guide" in catalog.text
    health_at = time.monotonic()
    assert client.get("/health").json() == {"status": "ok"}
    assert time.monotonic() - health_at < 1
    gate.set()
    for document_id in records:
        _wait_record(store, document_id, lambda item: item.status == "ready")


def test_unaccounted_blobs_are_deleted_on_startup(tmp_path: Path) -> None:
    store = FileDocumentStore(tmp_path)
    store.put_pdf("loose", b"%PDF-1.4 loose")
    store.put_pdf("kept", _pdf(title="Kept guide"))
    store.put_record(_stored("kept", "ready", title="Kept guide", source="kept.pdf"))
    claim = tmp_path / "indexing" / "loose-claim"
    claim.parent.mkdir()
    claim.write_text("x", encoding="utf-8")
    with TestClient(create_app(_settings(tmp_path), store)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "loose" in store.list_pdf_ids():
            time.sleep(0.02)
    assert store.list_pdf_ids() == ["kept"]
    assert store.get_record("kept") is not None
    assert not claim.exists()


def test_audit_table_starts_at_ten_and_loads_fifty_more(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    for number in range(70):
        store.put_audit(
            AuditEvent(
                id=f"20261016T000000{number:06d}-aaaaaaaa",
                at="2026-10-16T00:00:00+00:00",
                actor="admin",
                action="upload",
                document_id=None,
                detail=f"event-{number}",
            )
        )
    client = _app(tmp_path, store)
    csrf = _sign_in(client)
    del csrf
    page = client.get("/admin")
    assert "audit-scroll" in page.text
    assert "auditLimit += 50" in page.text
    assert "#audit-panel .audit-scroll { max-height: 18rem; overflow-y: auto; }" in page.text
    first = client.get("/admin/api/audit")
    assert first.status_code == 200
    assert 'data-shown="10"' in first.text
    assert 'data-total="70"' in first.text
    assert first.text.count("<tr>") == 11
    assert "event-69" in first.text
    assert "event-60" in first.text
    assert "event-59" not in first.text
    assert "Scroll to load more." in first.text
    wider = client.get("/admin/api/audit?limit=60")
    assert wider.text.count("<tr>") == 61
    assert "event-10" in wider.text
    assert "event-9" not in wider.text
    assert "Scroll to load more." in wider.text
    whole = client.get("/admin/api/audit?limit=110")
    assert whole.text.count("<tr>") == 71
    assert "event-0" in whole.text
    assert "Scroll to load more." not in whole.text
