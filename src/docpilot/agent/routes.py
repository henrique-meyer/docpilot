"""Public POST /agent. The same demo limits as /ask apply."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from docpilot.agent.service import Pilot
from docpilot.ask.limits import DemoLimitReached, QuestionTooLong, RateLimited
from docpilot.ask.routes import AskBody, _limits, client_ip
from docpilot.ask.service import DEMO_LIMIT

logger = logging.getLogger(__name__)
router = APIRouter()


def _pilot(request: Request) -> Pilot:
    pilot: Pilot = request.app.state.pilot
    return pilot


@router.post("/agent")
async def agent(body: AskBody, request: Request) -> JSONResponse:
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
            {"answer": DEMO_LIMIT, "citations": [], "trace_id": "", "tool_calls": []},
            status_code=429,
        )
    try:
        result = await _pilot(request).ask(question)
    except Exception:
        logger.exception("agent failed")
        return JSONResponse({"error": "unavailable"}, status_code=503)
    limits.add_tokens(result.tokens)
    payload = {
        "answer": result.answer,
        "citations": result.citations,
        "trace_id": result.trace_id,
        "tool_calls": result.tool_calls,
    }
    if result.timed_out:
        return JSONResponse(payload, status_code=504)
    return JSONResponse(payload)
