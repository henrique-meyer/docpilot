"""Read page count and extracted text size from an uploaded PDF."""

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from docpilot.documents.errors import DocumentError
from docpilot.documents.models import PdfInfo
from docpilot.ingest.repair import repair_passage

MAX_PAGES = 2000


def extract_pdf(data: bytes) -> PdfInfo:
    if not data.startswith(b"%PDF-"):
        raise DocumentError("not_pdf")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError("encrypted")
        page_count = len(reader.pages)
        if page_count < 1 or page_count > MAX_PAGES:
            raise DocumentError("too_large" if page_count > MAX_PAGES else "not_pdf")
        text_bytes = 0
        for page in reader.pages:
            text_bytes += len(repair_passage(page.extract_text() or "").encode("utf-8"))
    except DocumentError:
        raise
    except PdfReadError as exc:
        raise DocumentError("not_pdf") from exc
    except Exception as exc:
        raise DocumentError("not_pdf") from exc
    return PdfInfo(pages=page_count, index_bytes=text_bytes)
