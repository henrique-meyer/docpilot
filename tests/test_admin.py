import os
import re
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pytest import MonkeyPatch

from docpilot.admin.auth import hash_password
from docpilot.config import Settings
from docpilot.documents.models import PdfInfo
from docpilot.documents.store import FileDocumentStore, MemoryDocumentStore
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
    client = _app(tmp_path, index_limit_bytes=50)
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
