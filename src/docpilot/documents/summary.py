"""Draft a catalog title, summary, and sample questions from ready documents."""

from __future__ import annotations

import json
from typing import Protocol, cast

from docpilot.documents.errors import DocumentError
from docpilot.documents.models import (
    QUESTION_LIMIT,
    SUMMARY_LIMIT,
    TITLE_LIMIT,
    CatalogSummary,
    DocumentRecord,
)
from docpilot.documents.service import DocumentService
from docpilot.ingest.pdftext import read_pages

EXCERPT_CHARS = 1800
PROMPT_CHARS = 12000
SUMMARY_OPENING = "The uploaded set of documents contains"


class Chat(Protocol):
    def complete(self, prompt: str) -> tuple[str, int]: ...


def propose_summary(service: DocumentService, chat: Chat) -> CatalogSummary:
    excerpts = _excerpts(service)
    text, _tokens = chat.complete(_prompt(excerpts))
    return parse_proposal(text)


def parse_proposal(text: str) -> CatalogSummary:
    try:
        loaded: object = json.loads(_json_object(text))
    except json.JSONDecodeError as exc:
        raise DocumentError("draft_failed") from exc
    if not isinstance(loaded, dict):
        raise DocumentError("draft_failed")
    payload = cast(dict[object, object], loaded)
    title = _clip(_text_field(payload, "title"), TITLE_LIMIT)
    summary = _with_opening(_text_field(payload, "summary"))
    questions = payload.get("questions")
    if not isinstance(questions, list) or len(questions) != 3:
        raise DocumentError("draft_failed")
    parsed: list[str] = []
    for item in questions:
        if not isinstance(item, str):
            raise DocumentError("draft_failed")
        parsed.append(_clip(item, QUESTION_LIMIT))
    if not title or not summary or any(not item for item in parsed):
        raise DocumentError("draft_failed")
    return CatalogSummary(title, summary, (parsed[0], parsed[1], parsed[2]))


def _excerpts(service: DocumentService) -> str:
    ready = [record for record in service.list_documents() if record.status == "ready"]
    if not ready:
        raise DocumentError("no_documents")
    blocks: list[str] = []
    readable = False
    for record in ready:
        excerpt = _excerpt(_document_text(service, record))
        if excerpt:
            readable = True
        title = record.title or record.source
        year = record.year or ""
        blocks.append(f"Title: {title}\nYear: {year}\nText: {excerpt}")
    if not readable:
        raise DocumentError("unreadable")
    return "\n\n".join(blocks)[:PROMPT_CHARS]


def _document_text(service: DocumentService, record: DocumentRecord) -> str:
    data = service.store.get_pdf(record.id)
    if not data:
        return ""
    try:
        pages = read_pages(data)
    except DocumentError:
        return ""
    return "\n".join(page.text for page in pages)


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= EXCERPT_CHARS:
        return collapsed
    middle = len(collapsed) // 2
    return collapsed[:1200] + " … " + collapsed[middle : middle + 600]


def _prompt(excerpts: str) -> str:
    return (
        "You write the catalog card for a document assistant. "
        "Use only the documents below.\n"
        "Return JSON with keys title, summary, and questions.\n"
        "title: one line naming what all of the documents have in common.\n"
        "summary: two or three sentences on what the set contains, "
        "so a visitor knows what to ask. "
        f'The summary starts with "{SUMMARY_OPENING}".\n'
        "questions: exactly three short questions a visitor could ask "
        "that these documents can answer. Make each one specific to the text.\n"
        "JSON only.\n\n"
        f"{excerpts}"
    )


def _json_object(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise DocumentError("draft_failed")
    return raw[start : end + 1]


def _text_field(payload: dict[object, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise DocumentError("draft_failed")
    return value


def _with_opening(summary: str) -> str:
    text = " ".join(summary.split()).strip()
    prefix = SUMMARY_OPENING.casefold()
    if text.casefold().startswith(prefix):
        rest = text[len(SUMMARY_OPENING) :].lstrip(" \t:-—")
        text = f"{SUMMARY_OPENING} {rest}".strip() if rest else SUMMARY_OPENING
    elif text:
        text = f"{SUMMARY_OPENING} {_uncapitalize(text)}"
    return _clip(text, SUMMARY_LIMIT)


def _uncapitalize(text: str) -> str:
    if len(text) >= 2 and text[0].isupper() and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def _clip(value: str, limit: int) -> str:
    return " ".join(value.split()).strip()[:limit]
