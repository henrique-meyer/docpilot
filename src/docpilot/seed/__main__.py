"""Seed a sample set: `python -m docpilot.seed --set irs`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docpilot.config import Settings
from docpilot.documents.service import DocumentService
from docpilot.documents.store import build_store
from docpilot.ingest import build_ingestor
from docpilot.seed.load import SeedError, seed_set

ROOT = Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest one sample set.")
    parser.add_argument("--set", required=True, dest="set_name")
    parser.add_argument("--sample-root", type=Path, default=ROOT / "sample-docs")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    service = DocumentService(build_store(settings), settings, ingestor=build_ingestor(settings))
    try:
        added, skipped = seed_set(args.sample_root, args.set_name, service)
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for name in added:
        print(f"added {name}")
    for name in skipped:
        print(f"skipped {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
