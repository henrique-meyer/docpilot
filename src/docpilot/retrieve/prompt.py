"""Versioned grounded prompt. The file is the template the model sees."""

from __future__ import annotations

from pathlib import Path

from docpilot.ingest.repair import repair_passage
from docpilot.retrieve.context import Hit

PROMPT_VERSION = "answer_v1"
_TEMPLATE = Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt"


def build_prompt(question: str, hits: list[Hit]) -> str:
    template = _TEMPLATE.read_text(encoding="utf-8")
    context = render_context(hits)
    return _fill(template, question.strip(), context or "(none)")


def render_context(hits: list[Hit]) -> str:
    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        year = f" | {hit.year}" if hit.year else ""
        header = f"[{index}] {hit.title} | {hit.section} | p. {hit.page}{year}"
        blocks.append(f"{header}\n{repair_passage(hit.content)}")
    return "\n\n".join(blocks)


def _fill(template: str, question: str, context: str) -> str:
    if "{question}" not in template or "{context}" not in template:
        raise ValueError("prompt template is missing {question} or {context}")
    question_mark = "\x00question\x00"
    context_mark = "\x00context\x00"
    marked = template.replace("{question}", question_mark, 1).replace("{context}", context_mark, 1)
    return marked.replace(question_mark, question).replace(context_mark, context)
