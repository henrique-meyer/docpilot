"""Extract page text and a running section heading from a PDF."""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from docpilot.documents.errors import DocumentError
from docpilot.ingest.chunk import PageText
from docpilot.ingest.repair import repair_passage

MAX_PAGES = 2000
SAMPLE_PAGES = 3


def read_pages(data: bytes) -> list[PageText]:
    if not data.startswith(b"%PDF-"):
        raise DocumentError("not_pdf")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("encrypted")
        if len(reader.pages) < 1 or len(reader.pages) > MAX_PAGES:
            raise DocumentError("too_large" if len(reader.pages) > MAX_PAGES else "not_pdf")
        section = ""
        pages: list[PageText] = []
        for number, page in enumerate(reader.pages, start=1):
            raw = page.extract_text() or ""
            section, body = _section_and_body(raw, section)
            pages.append(PageText(page=number, section=section, text=body))
    except DocumentError:
        raise
    except PdfReadError as exc:
        raise DocumentError("not_pdf") from exc
    except Exception as exc:
        raise DocumentError("not_pdf") from exc
    return pages


def read_sample(data: bytes) -> str:
    """Text from the opening pages and one middle page."""
    if not data.startswith(b"%PDF-"):
        raise DocumentError("not_pdf")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("encrypted")
        count = len(reader.pages)
        if count < 1:
            raise DocumentError("not_pdf")
        parts: list[str] = []
        section = ""
        for index in _sample_indexes(count, SAMPLE_PAGES):
            raw = reader.pages[index].extract_text() or ""
            section, body = _section_and_body(raw, section)
            if body:
                parts.append(body)
    except DocumentError:
        raise
    except PdfReadError as exc:
        raise DocumentError("not_pdf") from exc
    except Exception as exc:
        raise DocumentError("not_pdf") from exc
    return "\n".join(parts)


def _sample_indexes(count: int, page_limit: int) -> list[int]:
    if count <= page_limit:
        return list(range(count))
    indexes = list(range(page_limit - 1))
    middle = count // 2
    if middle not in indexes:
        indexes.append(middle)
    return indexes


def _section_and_body(raw: str, current: str) -> tuple[str, str]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        return current, ""
    if _looks_like_heading(lines[0]):
        return repair_passage(lines[0])[:200], repair_passage("\n".join(lines[1:]))
    return current, repair_passage("\n".join(lines))


def _looks_like_heading(line: str) -> bool:
    if len(line) > 80 or len(line) < 3:
        return False
    if line.endswith("."):
        return False
    letters = [char for char in line if char.isalpha()]
    if not letters:
        return False
    upper = sum(1 for char in letters if char.isupper())
    return upper / len(letters) > 0.6
