"""Run a golden set and write the pass/fail report."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from docpilot.config import Settings
from docpilot.eval.cases import GoldenCase, RedTeamCase, load_golden, load_redteam
from docpilot.eval.score import Judge, citation_score, correctness, red_team_passed


class Answerer(Protocol):
    def ask(self, question: str) -> object: ...


@dataclass(frozen=True)
class Thresholds:
    correctness: float
    citation_accuracy: float
    groundedness: float
    red_team_pass_rate: float

    @classmethod
    def from_settings(cls, settings: Settings) -> Thresholds:
        return cls(
            settings.eval_correctness,
            settings.eval_citation_accuracy,
            settings.eval_groundedness,
            settings.eval_red_team_pass_rate,
        )


@dataclass(frozen=True)
class Row:
    id: str
    kind: str
    correctness: float | None
    citation_accuracy: float | None
    groundedness: float | None
    passed: bool | None
    latency_ms: float
    tokens: int


@dataclass(frozen=True)
class EvalReport:
    set_name: str
    rows: tuple[Row, ...]
    correctness: float
    citation_accuracy: float
    groundedness: float
    red_team_pass_rate: float
    mean_latency_ms: float
    total_tokens: int
    passed: bool
    failures: tuple[str, ...]


def evaluate(
    answerer: Answerer,
    judge: Judge,
    golden: list[GoldenCase],
    redteam: list[RedTeamCase],
    thresholds: Thresholds,
    *,
    set_name: str,
) -> EvalReport:
    rows: list[Row] = []
    for case in golden:
        result, latency = _ask(answerer, case.question)
        judged, grounded, judge_tokens = judge.score(case.question, result.answer, case.facts)
        rows.append(
            Row(
                case.id,
                "golden",
                correctness(result.answer, case, judged),
                citation_score(result.citations, case.sources),
                grounded,
                None,
                latency,
                result.tokens + judge_tokens,
            )
        )
    for prompt in redteam:
        result, latency = _ask(answerer, prompt.question)
        rows.append(
            Row(
                prompt.id,
                "redteam",
                None,
                None,
                None,
                red_team_passed(result.answer, result.citations, prompt),
                latency,
                result.tokens,
            )
        )
    return _summarize(set_name, rows, thresholds)


def write_report(report: EvalReport, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{report.set_name}.json"
    markdown_path = directory / f"{report.set_name}.md"
    json_path.write_text(json.dumps(_payload(report), indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _ask(answerer: Answerer, question: str) -> tuple[_Answer, float]:
    started = time.perf_counter()
    raw = answerer.ask(question)
    elapsed = (time.perf_counter() - started) * 1000
    answer = str(getattr(raw, "answer", ""))
    citations = getattr(raw, "citations", [])
    tokens = int(getattr(raw, "tokens", 0) or 0)
    if not isinstance(citations, list):
        citations = []
    typed: list[Mapping[str, object]] = [item for item in citations if isinstance(item, Mapping)]
    return _Answer(answer, typed, tokens), elapsed


@dataclass(frozen=True)
class _Answer:
    answer: str
    citations: list[Mapping[str, object]]
    tokens: int


def _summarize(set_name: str, rows: list[Row], thresholds: Thresholds) -> EvalReport:
    golden = [row for row in rows if row.kind == "golden"]
    redteam = [row for row in rows if row.kind == "redteam"]
    correct = _mean([row.correctness or 0.0 for row in golden])
    citations = _mean([row.citation_accuracy or 0.0 for row in golden])
    grounded = _mean([row.groundedness or 0.0 for row in golden])
    red_rate = _mean([1.0 if row.passed else 0.0 for row in redteam])
    failures: list[str] = []
    if correct < thresholds.correctness:
        failures.append(f"correctness {correct:.2f} < {thresholds.correctness:.2f}")
    if citations < thresholds.citation_accuracy:
        failures.append(f"citation accuracy {citations:.2f} < {thresholds.citation_accuracy:.2f}")
    if grounded < thresholds.groundedness:
        failures.append(f"groundedness {grounded:.2f} < {thresholds.groundedness:.2f}")
    if red_rate < thresholds.red_team_pass_rate:
        failures.append(f"red-team pass rate {red_rate:.2f} < {thresholds.red_team_pass_rate:.2f}")
    return EvalReport(
        set_name,
        tuple(rows),
        correct,
        citations,
        grounded,
        red_rate,
        _mean([row.latency_ms for row in rows]),
        sum(row.tokens for row in rows),
        not failures,
        tuple(failures),
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _payload(report: EvalReport) -> dict[str, object]:
    return {
        "set": report.set_name,
        "passed": report.passed,
        "failures": list(report.failures),
        "correctness": report.correctness,
        "citation_accuracy": report.citation_accuracy,
        "groundedness": report.groundedness,
        "red_team_pass_rate": report.red_team_pass_rate,
        "mean_latency_ms": report.mean_latency_ms,
        "total_tokens": report.total_tokens,
        "rows": [
            {
                "id": row.id,
                "kind": row.kind,
                "correctness": row.correctness,
                "citation_accuracy": row.citation_accuracy,
                "groundedness": row.groundedness,
                "passed": row.passed,
                "latency_ms": row.latency_ms,
                "tokens": row.tokens,
            }
            for row in report.rows
        ],
    }


def _markdown(report: EvalReport) -> str:
    status = "pass" if report.passed else "fail"
    lines = [
        f"# Eval {report.set_name}",
        "",
        f"Result: {status}",
        "",
        f"- correctness: {report.correctness:.2f}",
        f"- citation accuracy: {report.citation_accuracy:.2f}",
        f"- groundedness: {report.groundedness:.2f}",
        f"- red-team pass rate: {report.red_team_pass_rate:.2f}",
        f"- mean latency: {report.mean_latency_ms:.0f} ms",
        f"- token cost: {report.total_tokens}",
        "",
    ]
    if report.failures:
        lines.append("Failures:")
        lines.extend(f"- {item}" for item in report.failures)
        lines.append("")
    lines.append("| id | kind | correctness | citations | groundedness | red team | ms | tokens |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in report.rows:
        lines.append(
            "| {id} | {kind} | {correct} | {citations} | {grounded} | {passed} | {latency} | {tokens} |".format(
                id=row.id,
                kind=row.kind,
                correct=_cell(row.correctness),
                citations=_cell(row.citation_accuracy),
                grounded=_cell(row.groundedness),
                passed="" if row.passed is None else ("pass" if row.passed else "fail"),
                latency=f"{row.latency_ms:.0f}",
                tokens=row.tokens,
            )
        )
    lines.append("")
    return "\n".join(lines)


def _cell(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"


def load_set(root: Path, name: str) -> tuple[list[GoldenCase], list[RedTeamCase]]:
    directory = root / "eval" / name
    return load_golden(directory / "golden.jsonl"), load_redteam(directory / "redteam.jsonl")
