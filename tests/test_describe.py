from io import BytesIO

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from docpilot.documents.describe import read_title, read_year


def _blank(title: str = "") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if title:
        writer.add_metadata({"/Title": title})
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _with_text(text: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    page = writer.pages[0]
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = stream
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_title_and_year_stay_empty_when_the_pdf_has_neither() -> None:
    named = _blank("2025 Publication 587")
    assert read_title(named) == "2025 Publication 587"
    assert read_year(named) == "2025"
    blank = _blank()
    assert read_title(blank) == ""
    assert read_year(blank) is None


def test_a_year_in_the_body_is_not_stored() -> None:
    data = _with_text("Tax year 2019")
    assert read_title(data) == ""
    assert read_year(data) is None
