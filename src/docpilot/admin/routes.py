"""Admin pages and /admin/api document routes."""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from docpilot.admin.auth import (
    LoginGuard,
    clear_session,
    client_address,
    csrf_matches,
    password_matches,
    read_session,
    write_session,
)
from docpilot.config import Settings
from docpilot.documents.errors import MESSAGES, DocumentError
from docpilot.documents.fetch import download_pdf
from docpilot.documents.service import DocumentService

router = APIRouter()
_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
    autoescape=select_autoescape(["html"]),
)

NOTICES = {
    "upload_queued": "Upload started. The file is stored when its status is ready.",
    "replace_queued": "Replacement started. The previous file stays until the new one is stored.",
    "deleted": "Document deleted.",
    "logged_out": "Signed out.",
}


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value / (1024 * 1024):.1f} MB"


_TEMPLATES.filters["bytes"] = format_bytes


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _service(request: Request) -> DocumentService:
    service: DocumentService = request.app.state.documents
    return service


def _guard(request: Request) -> LoginGuard:
    guard: LoginGuard = request.app.state.login_guard
    return guard


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def _error_response(request: Request, code: str, status_code: int) -> Response:
    if _wants_json(request):
        return _no_store(JSONResponse({"error": code, "message": MESSAGES[code]}, status_code=status_code))
    return _no_store(RedirectResponse(f"/admin?error={code}", status_code=303))


def _render(name: str, status_code: int = 200, **context: object) -> HTMLResponse:
    body = _TEMPLATES.get_template(name).render(**context)
    return HTMLResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})


def _signed_in(request: Request) -> bool:
    session = read_session(request, _settings(request))
    return session is not None and session.subject == "admin"


def _login_page(
    *,
    status_code: int = 200,
    error: str = "",
    csrf: str = "",
    notice: str = "",
) -> HTMLResponse:
    return _render("login.html", status_code=status_code, error=error, csrf=csrf, notice=notice)


def _anonymous_denied(request: Request) -> Response:
    if _wants_json(request) or request.method == "GET":
        return _no_store(JSONResponse({"error": "unauthorized"}, status_code=401))
    return _no_store(RedirectResponse("/admin/login", status_code=303))


def _require_admin(request: Request) -> Response | None:
    settings = _settings(request)
    if not settings.admin_configured:
        if _wants_json(request) or request.url.path.startswith("/admin/api"):
            return _no_store(JSONResponse({"error": "not_configured"}, status_code=503))
        return _login_page(status_code=503, error=MESSAGES["not_configured"])
    if not _signed_in(request):
        if request.url.path.startswith("/admin/api"):
            return _anonymous_denied(request)
        return _no_store(RedirectResponse("/admin/login", status_code=303))
    return None


def _require_csrf(request: Request, provided: str) -> Response | None:
    session = read_session(request, _settings(request))
    if csrf_matches(session, provided):
        return None
    if _wants_json(request):
        return _no_store(JSONResponse({"error": "csrf", "message": MESSAGES["csrf"]}, status_code=403))
    if request.url.path.startswith("/admin/api"):
        return _no_store(RedirectResponse("/admin?error=csrf", status_code=303))
    return _login_page(status_code=403, error=MESSAGES["csrf"], csrf=session.csrf if session else "")


@router.get("/admin/login")
def login_form(request: Request) -> Response:
    settings = _settings(request)
    notice = NOTICES.get(request.query_params.get("notice", ""), "")
    if not settings.admin_configured:
        return _login_page(status_code=503, error=MESSAGES["not_configured"], notice=notice)
    if _signed_in(request):
        return RedirectResponse("/admin", status_code=303)
    probe = Response()
    csrf = write_session(probe, settings, None)
    page = _login_page(csrf=csrf, notice=notice)
    page.headers["set-cookie"] = probe.headers["set-cookie"]
    return page


@router.post("/admin/login")
def login(request: Request, password: str = Form(""), csrf: str = Form("")) -> Response:
    settings = _settings(request)
    if not settings.admin_configured:
        return _login_page(status_code=503, error=MESSAGES["not_configured"])
    denied = _require_csrf(request, csrf)
    if denied is not None:
        return denied
    client = client_address(request)
    guard = _guard(request)
    if guard.remaining_lock(client) > 0:
        session = read_session(request, settings)
        return _login_page(
            status_code=429,
            error=MESSAGES["lockout"],
            csrf=session.csrf if session else "",
        )
    if not password_matches(settings.admin_password_hash, password):
        guard.record_failure(client)
        session = read_session(request, settings)
        status = 429 if guard.remaining_lock(client) > 0 else 401
        message = MESSAGES["lockout"] if status == 429 else MESSAGES["bad_password"]
        return _login_page(
            status_code=status,
            error=message,
            csrf=session.csrf if session else "",
        )
    guard.record_success(client)
    if _wants_json(request):
        response: Response = JSONResponse({"ok": True})
    else:
        response = RedirectResponse("/admin", status_code=303)
    write_session(response, settings, "admin")
    return _no_store(response)


@router.post("/admin/logout")
def logout(request: Request, csrf: str = Form("")) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    denied = _require_csrf(request, csrf)
    if denied is not None:
        return denied
    response = RedirectResponse("/admin/login?notice=logged_out", status_code=303)
    clear_session(response, _settings(request))
    return _no_store(response)


@router.get("/admin")
def admin_page(request: Request) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    session = read_session(request, _settings(request))
    service = _service(request)
    error_code = request.query_params.get("error", "")
    notice_code = request.query_params.get("notice", "")
    documents = service.list_documents()
    return _render(
        "admin.html",
        csrf=session.csrf if session else "",
        documents=documents,
        audit=service.list_audit(),
        index_bytes=service.index_bytes_used(),
        source_bytes=service.source_bytes_used(),
        index_limit=service.settings.index_limit_bytes,
        percent=_percent(service.index_bytes_used(), service.settings.index_limit_bytes),
        error=MESSAGES.get(error_code, ""),
        notice=NOTICES.get(notice_code, ""),
        processing=any(item.status in {"queued", "processing"} for item in documents),
    )


@router.get("/admin/api/documents")
def list_documents(request: Request) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    service = _service(request)
    return _no_store(
        JSONResponse(
            {
                "documents": [record.to_json() for record in service.list_documents()],
                "index_bytes": service.index_bytes_used(),
                "index_limit_bytes": service.settings.index_limit_bytes,
                "source_bytes": service.source_bytes_used(),
            }
        )
    )


@router.post("/admin/api/documents")
async def upload_document(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile | None = File(None),
    title: str = Form(""),
    year: str = Form(""),
    csrf: str = Form(""),
) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    denied = _require_csrf(request, csrf)
    if denied is not None:
        return denied
    data = await file.read() if file is not None else b""
    source = Path(file.filename or "upload.pdf").name if file is not None else "upload.pdf"
    return _queue_upload(
        request,
        background,
        data=data,
        title=title,
        source=source,
        year=year,
        notice="upload_queued",
    )


@router.post("/admin/api/documents/url")
def upload_url(
    request: Request,
    background: BackgroundTasks,
    url: str = Form(""),
    title: str = Form(""),
    year: str = Form(""),
    csrf: str = Form(""),
) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    denied = _require_csrf(request, csrf)
    if denied is not None:
        return denied
    try:
        data = download_pdf(url, _settings(request).max_upload_bytes)
    except DocumentError as exc:
        return _error_response(request, exc.code, 400)
    return _queue_upload(
        request,
        background,
        data=data,
        title=title,
        source=url.strip(),
        year=year,
        notice="upload_queued",
    )


@router.post("/admin/api/documents/{document_id}/replace")
async def replace_document(
    document_id: str,
    request: Request,
    background: BackgroundTasks,
    file: UploadFile | None = File(None),
    title: str = Form(""),
    year: str = Form(""),
    csrf: str = Form(""),
) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    denied = _require_csrf(request, csrf)
    if denied is not None:
        return denied
    data = await file.read() if file is not None else b""
    service = _service(request)
    try:
        service.validate_upload(data)
        if service.store.get_record(document_id) is None:
            raise DocumentError("not_found")
    except DocumentError as exc:
        status = 404 if exc.code == "not_found" else 400
        return _error_response(request, exc.code, status)
    background.add_task(service.finish_replace, document_id, data, title=title, year=year)
    return _accepted(request, "replace_queued")


@router.post("/admin/api/documents/{document_id}/delete")
def delete_document(document_id: str, request: Request, csrf: str = Form("")) -> Response:
    return _delete(document_id, request, csrf)


@router.delete("/admin/api/documents/{document_id}")
def delete_document_method(document_id: str, request: Request, csrf: str = Form("")) -> Response:
    return _delete(document_id, request, csrf)


def _delete(document_id: str, request: Request, csrf: str) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    denied = _require_csrf(request, csrf)
    if denied is not None:
        return denied
    try:
        _service(request).delete(document_id)
    except DocumentError as exc:
        return _error_response(request, exc.code, 404)
    if _wants_json(request):
        return _no_store(JSONResponse({"ok": True}))
    return _no_store(RedirectResponse("/admin?notice=deleted", status_code=303))


def _queue_upload(
    request: Request,
    background: BackgroundTasks,
    *,
    data: bytes,
    title: str,
    source: str,
    year: str,
    notice: str,
) -> Response:
    service = _service(request)
    try:
        record = service.begin_upload(data, title=title, source=source, year=year)
    except DocumentError as exc:
        return _error_response(request, exc.code, 400)
    background.add_task(service.finish_upload, record.id, data)
    return _accepted(request, notice, record.to_json())


def _accepted(request: Request, notice: str, body: dict[str, object] | None = None) -> Response:
    if _wants_json(request):
        payload: dict[str, object] = {"status": "queued"}
        if body is not None:
            payload = body
        return _no_store(JSONResponse(payload, status_code=202))
    return _no_store(RedirectResponse(f"/admin?notice={notice}", status_code=303))


def _percent(used: int, limit: int) -> int:
    if limit <= 0:
        return 100
    return min(100, round(used * 100 / limit))
