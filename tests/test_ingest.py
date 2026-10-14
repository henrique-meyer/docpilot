from docpilot.ingest.chunk import PageText, chunk_pages
from docpilot.ingest.pdftext import read_pages
from docpilot.ingest.pipeline import Ingestor, MemoryIndex


def test_chunks_keep_page_section_and_stable_ids() -> None:
    pages = [
        PageText(page=1, section="Home office", text="rules " * 40),
        PageText(page=2, section="Home office", text="deduction " * 40),
    ]
    first = chunk_pages(
        pages,
        document_id="doc",
        title="Pub 587",
        source="p587.pdf",
        year="2025",
        chunk_tokens=20,
        overlap_tokens=4,
    )
    second = chunk_pages(
        pages,
        document_id="doc",
        title="Pub 587",
        source="p587.pdf",
        year="2025",
        chunk_tokens=20,
        overlap_tokens=4,
    )
    assert first
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]
    assert {chunk.page for chunk in first} == {1, 2}
    assert all(chunk.section == "Home office" for chunk in first)
    assert all(chunk.document_id == "doc" for chunk in first)


def test_blank_page_produces_no_chunks() -> None:
    chunks = chunk_pages(
        [PageText(page=1, section="", text="   ")],
        document_id="doc",
        title="Empty",
        source="empty.pdf",
        year=None,
    )
    assert chunks == []


def test_replace_drops_old_chunk_ids() -> None:
    index = MemoryIndex()
    ingestor = Ingestor(None, index, chunk_tokens=30, overlap_tokens=4)
    text_pdf = _pdf_with_text("HOME OFFICE\nThe deduction follows the exclusive use rule. " * 8)
    first = ingestor.ingest(
        text_pdf,
        document_id="doc",
        title="Pub 587",
        source="p587.pdf",
        year="2025",
    )
    assert first.chunk_count == len(index.docs)
    assert first.chunk_count > 0
    shorter = _pdf_with_text("HOME OFFICE\nShort note.")
    second = ingestor.ingest(
        shorter,
        document_id="doc",
        title="Pub 587",
        source="p587.pdf",
        year="2025",
    )
    assert second.chunk_count == len(index.docs)
    assert second.chunk_count < first.chunk_count
    ingestor.delete("doc")
    assert index.docs == {}


def test_heading_becomes_the_section() -> None:
    pages = read_pages(_pdf_with_text("HOME OFFICE\nExclusive use is required."))
    assert pages[0].section == "HOME OFFICE"
    assert "Exclusive use" in pages[0].text


def _pdf_with_text(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
    content = b"<<" + f"/Length {len(stream)}".encode() + b">>\nstream\n" + stream + b"\nendstream"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        content,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode())
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode())
    body.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(body)
