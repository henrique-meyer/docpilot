"""Public question page and POST /ask."""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field

from docpilot.analytics import analytics_head
from docpilot.ask.limits import DemoLimitReached, DemoLimits, QuestionTooLong, RateLimited
from docpilot.ask.service import DEMO_LIMIT, Asker
from docpilot.documents.service import DocumentService

logger = logging.getLogger(__name__)
router = APIRouter()
_SOURCE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent),
    autoescape=select_autoescape(["html"]),
)
_TEMPLATES.globals["analytics_head"] = analytics_head


class AskBody(BaseModel):
    question: str = Field(min_length=1)


def _limits(request: Request) -> DemoLimits:
    limits: DemoLimits = request.app.state.limits
    return limits


def _asker(request: Request) -> Asker:
    asker: Asker = request.app.state.asker
    return asker


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client is None:
        return "unknown"
    return request.client.host


def _documents(request: Request) -> DocumentService:
    service: DocumentService = request.app.state.documents
    return service


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    summary = _documents(request).get_summary()
    questions = [question for question in summary.questions if question]
    body = _TEMPLATES.get_template("page.html").render(
        summary=summary,
        questions=questions,
        show_summary=bool(summary.title or summary.summary or questions),
    )
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request) -> HTMLResponse:
    service = _documents(request)
    summary = service.get_summary()
    body = _TEMPLATES.get_template("documents.html").render(
        summary=summary,
        documents=service.list_documents(),
    )
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/sources/{document_id}")
def source_pdf(document_id: str, request: Request) -> Response:
    if _SOURCE_ID.fullmatch(document_id) is None:
        return Response(status_code=404)
    documents = _documents(request)
    record = documents.store.get_record(document_id)
    data = documents.store.get_pdf(document_id) if record is not None else None
    if record is None or data is None:
        return Response(status_code=404)
    filename = _pdf_filename(record.source)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _pdf_filename(source: str) -> str:
    name = source.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    cleaned = cleaned.strip("._") or "document"
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned}.pdf"
    return cleaned[:120]


@router.post("/ask")
def ask(body: AskBody, request: Request) -> JSONResponse:
    question = body.question.strip()
    if not question:
        return JSONResponse({"error": "question_required"}, status_code=400)
    limits = _limits(request)
    try:
        limits.check_question(question)
        limits.consume_request(client_ip(request))
    except QuestionTooLong:
        return JSONResponse({"error": "question_too_long"}, status_code=400)
    except RateLimited:
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    except DemoLimitReached:
        return JSONResponse(
            {"answer": DEMO_LIMIT, "citations": [], "trace_id": ""},
            status_code=429,
        )
    try:
        result = _asker(request).ask(question)
    except Exception:
        # A search or model outage must not look like an empty corpus.
        logger.exception("ask failed")
        return JSONResponse({"error": "unavailable"}, status_code=503)
    limits.add_tokens(result.tokens)
    return JSONResponse(
        {"answer": result.answer, "citations": result.citations, "trace_id": result.trace_id}
    )
