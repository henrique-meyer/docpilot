import json
from pathlib import Path

from docpilot.ask.service import AskResult
from docpilot.eval.cases import GoldenCase, RedTeamCase, Source, load_golden, load_redteam
from docpilot.eval.runner import Thresholds, evaluate, self_check, write_report
from docpilot.eval.score import citation_score, fact_score, red_team_passed

ROOT = Path(__file__).resolve().parents[1]
PASSING = Thresholds(0.8, 0.9, 0.8, 1.0)


class Scripted:
    def __init__(self, answers: dict[str, AskResult]) -> None:
        self.answers = answers

    def ask(self, question: str) -> AskResult:
        return self.answers[question]


class PerfectJudge:
    def score(self, question: str, answer: str, facts: tuple[str, ...]) -> tuple[float, float, int]:
        del question, answer, facts
        return 1.0, 1.0, 3


def test_irs_self_check_passes_the_configured_thresholds() -> None:
    report = self_check(ROOT, "irs", PASSING)
    assert report.passed
    assert report.correctness == 1
    assert report.citation_accuracy == 1
    assert report.red_team_pass_rate == 1


def test_irs_set_has_thirty_questions_and_a_multi_document_case() -> None:
    golden = load_golden(ROOT / "eval" / "irs" / "golden.jsonl")
    redteam = load_redteam(ROOT / "eval" / "irs" / "redteam.jsonl")
    assert len(golden) == 30
    assert len(redteam) == 10
    assert any(len(case.sources) > 1 for case in golden)
    ids = [case.id for case in golden] + [case.id for case in redteam]
    assert len(ids) == len(set(ids))


def test_fact_and_citation_scores() -> None:
    assert fact_score("The test is exclusive use of a room.", ("exclusive use",)) == 1
    assert fact_score("No numbers here.", ("$15,750",)) == 0
    citations = [{"doc": "2025 Publication 587", "page": 2, "section": "Exclusive Use", "snippet": "x"}]
    sources = (Source("Publication 587", (2,)),)
    assert citation_score(citations, sources) == 1
    assert citation_score([{"doc": "2025 Publication 587", "page": 9}], sources) == 0


def test_red_team_rejects_a_forbidden_phrase() -> None:
    case = RedTeamCase("rt", "q", False, ("swordfish",))
    assert red_team_passed("not found in the documents", [], case)
    assert not red_team_passed("swordfish", [], case)


def test_low_scores_fail_and_the_report_is_written(tmp_path: Path) -> None:
    case = GoldenCase("q1", "home office", ("exclusive use",), (Source("Publication 587", (2,)),))
    attack = RedTeamCase("rt", "capital", True, ())
    answerer = Scripted(
        {
            "home office": AskResult("I do not know.", [], "trace", 4),
            "capital": AskResult("Paris.", [], "trace", 2),
        }
    )
    report = evaluate(answerer, PerfectJudge(), [case], [attack], PASSING, set_name="irs")
    assert not report.passed
    assert report.total_tokens == 4 + 3 + 2
    json_path, markdown_path = write_report(report, tmp_path)
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["passed"] is False
    assert "correctness" in markdown_path.read_text(encoding="utf-8")


def test_a_grounded_answer_passes() -> None:
    case = GoldenCase("q1", "home office", ("exclusive use",), (Source("Publication 587", (2,)),))
    attack = RedTeamCase("rt", "capital", True, ())
    answerer = Scripted(
        {
            "home office": AskResult(
                "The exclusive use test applies.",
                [{"doc": "2025 Publication 587", "page": 2}],
                "trace",
                5,
            ),
            "capital": AskResult("not found in the documents", [], "trace", 1),
        }
    )
    report = evaluate(answerer, PerfectJudge(), [case], [attack], PASSING, set_name="irs")
    assert report.passed
    assert report.correctness == 1
    assert report.citation_accuracy == 1
    assert report.red_team_pass_rate == 1
