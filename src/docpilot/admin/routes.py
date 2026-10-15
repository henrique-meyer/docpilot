"""Admin pages and /admin/api document routes."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from docpilot.admin.auth import (
    FLASH,
    LoginGuard,
    clear_flash,
    clear_session,
    client_address,
    csrf_matches,
    password_matches,
    read_flash,
    read_session,
    write_flash,
    write_session,
)
from docpilot.admin.bootstrap import MIN_PASSWORD_CHARS, AdminLogin
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
    "upload_queued": "The file is stored. Indexing continues until the status is ready.",
    "replace_queued": "Replacement started. The previous file stays until the new one is stored.",
    "deleted": "Document deleted.",
    "reset": "Catalog, documents, and the audit log were deleted.",
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
    login: AdminLogin | None = getattr(request.app.state, "admin_login", None)
    if login is not None:
        return login.settings
    settings: Settings = request.app.state.settings
    return settings


def _login_state(request: Request) -> AdminLogin:
    login: AdminLogin = request.app.state.admin_login
    return login


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
    return _redirect(request, "/admin", error=code)


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
    setup: bool = False,
) -> HTMLResponse:
    return _render(
        "login.html",
        status_code=status_code,
        error=error,
        csrf=csrf,
        notice=notice,
        setup=setup,
    )


def _attach_cookie(page: HTMLResponse, probe: Response) -> HTMLResponse:
    raw = probe.headers.get("set-cookie")
    if raw:
        page.headers.append("set-cookie", raw)
    return page


def _take_flash(request: Request) -> tuple[str, str, bool]:
    settings = _settings(request)
    present = FLASH in request.cookies
    found = read_flash(request, settings)
    notice = ""
    error = ""
    if found is not None:
        kind, code = found
        if kind == "notice":
            notice = NOTICES.get(code, "")
        else:
            error = MESSAGES.get(code, "")
    return notice, error, present


def _redirect(request: Request, location: str, *, notice: str = "", error: str = "") -> Response:
    response = RedirectResponse(location, status_code=303)
    if notice:
        write_flash(response, _settings(request), "notice", notice)
    elif error:
        write_flash(response, _settings(request), "error", error)
    return _no_store(response)


def _anonymous_denied(request: Request) -> Response:
    if _wants_json(request) or request.method == "GET":
        return _no_store(JSONResponse({"error": "unauthorized"}, status_code=401))
    return _no_store(RedirectResponse("/admin/login", status_code=303))


def _require_admin(request: Request) -> Response | None:
    settings = _settings(request)
    if not settings.admin_configured:
        if _wants_json(request) or request.url.path.startswith("/admin/api"):
            return _no_store(JSONResponse({"error": "not_configured"}, status_code=503))
        return _no_store(RedirectResponse("/admin/login", status_code=303))
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
        return _redirect(request, "/admin", error="csrf")
    return _login_page(status_code=403, error=MESSAGES["csrf"], csrf=session.csrf if session else "")


@router.get("/admin/login")
def login_form(request: Request) -> Response:
    settings = _settings(request)
    if not settings.admin_configured:
        return _setup_form(request)
    if _signed_in(request):
        return RedirectResponse("/admin", status_code=303)
    notice, error, present = _take_flash(request)
    probe = Response()
    csrf = write_session(probe, settings, None)
    page = _login_page(csrf=csrf, notice=notice, error=error)
    if present:
        clear_flash(page, settings)
    return _attach_cookie(page, probe)


def _setup_form(request: Request, *, error: str = "", status_code: int = 200) -> HTMLResponse:
    signing = _login_state(request).prepare()
    notice, flash_error, present = _take_flash(request)
    if not error:
        error = flash_error
    session = read_session(request, signing)
    if session is None:
        probe = Response()
        csrf = write_session(probe, signing, None)
        page = _login_page(status_code=status_code, error=error, csrf=csrf, notice=notice, setup=True)
        if present:
            clear_flash(page, signing)
        return _attach_cookie(page, probe)
    page = _login_page(
        status_code=status_code,
        error=error,
        csrf=session.csrf,
        notice=notice,
        setup=True,
    )
    if present:
        clear_flash(page, signing)
    return page


@router.post("/admin/setup")
def setup_admin(
    request: Request,
    password: str = Form(""),
    confirm: str = Form(""),
    csrf: str = Form(""),
) -> Response:
    login = _login_state(request)
    if login.settings.admin_configured:
        return _no_store(RedirectResponse("/admin/login", status_code=303))
    signing = login.prepare()
    session = read_session(request, signing)
    if not csrf_matches(session, csrf):
        return _setup_form(request, error=MESSAGES["csrf"], status_code=403)
    if len(password) < MIN_PASSWORD_CHARS:
        return _setup_form(request, error=MESSAGES["password_short"], status_code=400)
    if password != confirm:
        return _setup_form(request, error=MESSAGES["password_mismatch"], status_code=400)
    if not login.claim(password):
        if not login.settings.admin_configured:
            return _setup_form(request, error=MESSAGES["already_set"], status_code=409)
        probe = Response()
        token = write_session(probe, login.settings, None)
        page = _login_page(error=MESSAGES["already_set"], csrf=token)
        return _attach_cookie(page, probe)
    response = RedirectResponse("/admin", status_code=303)
    write_session(response, login.settings, "admin")
    return _no_store(response)


@router.post("/admin/login")
def login(request: Request, password: str = Form(""), csrf: str = Form("")) -> Response:
    settings = _settings(request)
    if not settings.admin_configured:
        return _setup_form(request)
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
    response = _redirect(request, "/admin/login", notice="logged_out")
    clear_session(response, _settings(request))
    return response


@router.get("/admin")
def admin_page(request: Request) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    session = read_session(request, _settings(request))
    service = _service(request)
    notice, error, present = _take_flash(request)
    documents = service.list_documents()
    processing = any(item.status in {"queued", "processing"} for item in documents)
    page = _render(
        "admin.html",
        csrf=session.csrf if session else "",
        documents=documents,
        audit=service.list_audit(),
        index_bytes=service.index_bytes_used(),
        source_bytes=service.source_bytes_used(),
        index_limit=service.settings.index_limit_bytes,
        percent=_percent(service.index_bytes_used(), service.settings.index_limit_bytes),
        error=error,
        notice=notice,
        processing=processing,
    )
    if present and not processing:
        clear_flash(page, _settings(request))
    return page


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
    file: Annotated[UploadFile | None, File()] = None,
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
    file: Annotated[UploadFile | None, File()] = None,
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


@router.get("/admin/reset")
def reset_page(request: Request) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    session = read_session(request, _settings(request))
    return _render(
        "reset.html",
        csrf=session.csrf if session else "",
    )


@router.post("/admin/reset")
def reset_catalog(request: Request, csrf: str = Form(""), confirm: str = Form("")) -> Response:
    denied = _require_admin(request)
    if denied is not None:
        return denied
    denied = _require_csrf(request, csrf)
    if denied is not None:
        return denied
    if confirm != "yes":
        return _no_store(RedirectResponse("/admin", status_code=303))
    _service(request).reset()
    return _redirect(request, "/admin", notice="reset")


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
    return _redirect(request, "/admin", notice="deleted")


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
    return _redirect(request, "/admin", notice=notice)


def _percent(used: int, limit: int) -> int:
    if limit <= 0:
        return 100
    return min(100, round(used * 100 / limit))
