"""Title and year taken from a PDF. Upload and seed share this reading.

Both stay empty when the PDF metadata does not have them. The file name is
not copied into the title, and a year printed in the body is not guessed.
"""

from __future__ import annotations

import re
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def read_title(data: bytes) -> str:
    return _metadata_title(data)[:200]


def read_year(data: bytes) -> str | None:
    return _find_year(_metadata_title(data))


def _metadata_title(data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data))
        meta = reader.metadata
        title = meta.title if meta is not None else None
    except (PdfReadError, ValueError, OSError):
        return ""
    if not isinstance(title, str):
        return ""
    return " ".join(title.split()).strip()


def _find_year(text: str) -> str | None:
    match = _YEAR.search(text)
    if match is None:
        return None
    return match.group(0)
