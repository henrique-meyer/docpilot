"""Compare DocPilot with a LangChain RAG chain on the same golden set.

Run: python eval/langchain_baseline.py --set irs
Needs AZURE_OPENAI_ENDPOINT and AZURE_SEARCH_ENDPOINT. This is not the production path.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from docpilot.ask.service import Asker, build_chat
from docpilot.config import Settings
from docpilot.eval.baseline import LangChainAnswerer, build_llm, compare_markdown
from docpilot.eval.runner import Thresholds, evaluate, load_set
from docpilot.eval.score import ChatJudge
from docpilot.retrieve.retriever import build_retriever


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Compare DocPilot with a LangChain baseline.")
    parser.add_argument("--set", default="irs", dest="set_name")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "eval" / "reports")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    chat = build_chat(settings)
    if chat is None or not settings.search_endpoint:
        print(
            "baseline needs AZURE_OPENAI_ENDPOINT and AZURE_SEARCH_ENDPOINT",
            file=sys.stderr,
        )
        return 2
    golden, redteam = load_set(ROOT, args.set_name)
    retriever = build_retriever(settings)
    judge = ChatJudge(chat)
    thresholds = Thresholds.from_settings(settings)
    docpilot = evaluate(
        Asker(retriever, chat),
        judge,
        golden,
        redteam,
        thresholds,
        set_name=args.set_name,
    )
    baseline = evaluate(
        LangChainAnswerer(retriever, build_llm(settings)),
        judge,
        golden,
        redteam,
        thresholds,
        set_name=args.set_name,
    )
    args.report_dir.mkdir(parents=True, exist_ok=True)
    path = args.report_dir / f"{args.set_name}-compare.md"
    path.write_text(compare_markdown(docpilot, baseline), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
