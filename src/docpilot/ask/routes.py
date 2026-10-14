"""Public question page and POST /ask."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from docpilot.ask.limits import DemoLimitReached, DemoLimits, QuestionTooLong, RateLimited
from docpilot.ask.service import DEMO_LIMIT, Asker

router = APIRouter()
_PAGE = Path(__file__).resolve().parent / "page.html"


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


@router.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))


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
    except Exception:  # noqa: BLE001
        # A search or model outage must not look like an empty corpus.
        return JSONResponse({"error": "unavailable"}, status_code=503)
    limits.add_tokens(result.tokens)
    return JSONResponse(
        {"answer": result.answer, "citations": result.citations, "trace_id": result.trace_id}
    )
