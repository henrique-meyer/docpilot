"""Download the PDFs named in a sample catalog. The files stay out of git."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.request import urlopen

from docpilot.seed.load import SeedError

_LINE = re.compile(r"^- (?P<name>[^/\s]+\.pdf): (?P<url>https://\S+)")
_MAX_BYTES = 40 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[3]


def catalog_files(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _LINE.match(line.strip())
        if match is None:
            continue
        rows.append((match.group("name"), match.group("url")))
    return rows


def fetch_set(
    sample_root: Path,
    set_name: str,
    *,
    opener: Callable[[str], bytes] | None = None,
) -> tuple[list[str], list[str]]:
    directory = sample_root / set_name
    catalog = directory / "SOURCES.md"
    if not catalog.is_file():
        raise SeedError(f"sample set {set_name} was not found")
    rows = catalog_files(catalog.read_text(encoding="utf-8"))
    if not rows:
        raise SeedError(f"sample set {set_name} has no PDF links")
    read = opener or _download
    downloaded: list[str] = []
    skipped: list[str] = []
    for name, url in rows:
        path = directory / name
        if path.is_file() and path.read_bytes()[:5] == b"%PDF-":
            skipped.append(name)
            continue
        data = read(url)
        if not data.startswith(b"%PDF-") or len(data) > _MAX_BYTES:
            raise SeedError(f"{name} is not a PDF")
        path.write_bytes(data)
        downloaded.append(name)
    return downloaded, skipped


def _download(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        final = response.geturl()
        if not isinstance(final, str) or not final.startswith("https://"):
            raise SeedError("refusing a non-https download")
        payload = response.read(_MAX_BYTES + 1)
        if not isinstance(payload, bytes):
            raise SeedError("download failed")
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download one sample set.")
    parser.add_argument("--set", required=True, dest="set_name")
    parser.add_argument("--sample-root", type=Path, default=ROOT / "sample-docs")
    args = parser.parse_args(argv)
    try:
        downloaded, skipped = fetch_set(args.sample_root, args.set_name)
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for name in downloaded:
        print(f"downloaded {name}")
    for name in skipped:
        print(f"skipped {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
