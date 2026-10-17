"""IRS eval command: `python -m docpilot.eval`."""

import argparse
import sys
from pathlib import Path

from docpilot.ask.service import Asker, build_chat
from docpilot.config import Settings
from docpilot.eval.runner import Thresholds, evaluate, load_set, self_check, write_report
from docpilot.eval.score import ChatJudge
from docpilot.retrieve.retriever import build_retriever

ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a sample set against the ask API.")
    parser.add_argument("--set", default="irs", dest="set_name")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "eval" / "reports")
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Score the golden file against itself. This checks the harness. It does not call the cloud index.",
    )
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.self_check:
        report = self_check(ROOT, args.set_name, Thresholds.from_settings(settings))
        json_path, markdown_path = write_report(report, args.report_dir)
        print(f"{'pass' if report.passed else 'fail'} {json_path} {markdown_path}")
        for failure in report.failures:
            print(failure, file=sys.stderr)
        return 0 if report.passed else 1
    chat = build_chat(settings)
    if chat is None or not settings.search_endpoint:
        print("eval needs AZURE_OPENAI_ENDPOINT and AZURE_SEARCH_ENDPOINT", file=sys.stderr)
        return 2
    golden, redteam = load_set(ROOT, args.set_name)
    report = evaluate(
        Asker(build_retriever(settings), chat),
        ChatJudge(chat),
        golden,
        redteam,
        Thresholds.from_settings(settings),
        set_name=args.set_name,
    )
    json_path, markdown_path = write_report(report, args.report_dir)
    print(f"{'pass' if report.passed else 'fail'} {json_path} {markdown_path}")
    for failure in report.failures:
        print(failure, file=sys.stderr)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
