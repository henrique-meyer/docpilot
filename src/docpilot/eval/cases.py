"""Load a sample set's golden questions and red-team prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    doc: str
    pages: tuple[int, ...]


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    facts: tuple[str, ...]
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class RedTeamCase:
    id: str
    question: str
    expect_not_found: bool
    forbid: tuple[str, ...]


def load_golden(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for row in _rows(path):
        sources = tuple(_source(item) for item in _dicts(row.get("sources"), "sources"))
        cases.append(
            GoldenCase(
                id=str(row["id"]),
                question=str(row["question"]),
                facts=tuple(_strings(row.get("facts"), "facts")),
                sources=sources,
            )
        )
    if not cases:
        raise ValueError(f"{path} has no questions")
    return cases


def load_redteam(path: Path) -> list[RedTeamCase]:
    cases: list[RedTeamCase] = []
    for row in _rows(path):
        expect = bool(row.get("expect_not_found", False))
        forbid = _strings(row.get("forbid", []), "forbid")
        if not expect and not forbid:
            raise ValueError(f"{row.get('id')} needs expect_not_found or forbid")
        cases.append(
            RedTeamCase(
                id=str(row["id"]),
                question=str(row["question"]),
                expect_not_found=expect,
                forbid=forbid,
            )
        )
    if not cases:
        raise ValueError(f"{path} has no prompts")
    return cases


def _source(item: dict[str, object]) -> Source:
    pages = item.get("pages", [])
    if not isinstance(pages, list):
        raise TypeError("pages must be a list")
    return Source(str(item.get("doc", "")), tuple(int(page) for page in pages))


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{label} must be a list of strings")
    return tuple(value)


def _dicts(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a list")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError(f"{label} must contain objects")
        rows.append(item)
    return rows


def _rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise TypeError(f"{path} contains a non-object row")
        rows.append(parsed)
    return rows
