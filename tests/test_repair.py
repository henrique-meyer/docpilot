from pathlib import Path

from pypdf import PdfReader

from docpilot.ask.service import _citation
from docpilot.ingest.repair import repair_passage
from docpilot.retrieve.context import Hit
from docpilot.retrieve.prompt import render_context

ROOT = Path(__file__).resolve().parents[1]


def test_line_break_hyphen_joins_the_word() -> None:
    raw = "your digi-\ntal wallet, or your online account.\nRecent legis-\nlation passed."
    assert (
        repair_passage(raw)
        == "your digital wallet, or your online account. Recent legislation passed."
    )


def test_stored_snippet_joins_a_hyphen_that_already_has_a_space() -> None:
    raw = "digi- tal wallet. legis- lation. ba- sis. Instruc- tions. rec- reation. pro- vided."
    assert repair_passage(raw) == (
        "digital wallet. legislation. basis. Instructions. recreation. provided."
    )


def test_compound_hyphen_stays() -> None:
    assert repair_passage("a 7-year holding period") == "a 7-year holding period"
    assert repair_passage("non-\ntaxable income") == "non-taxable income"
    assert repair_passage("non -itemizers and self -employed") == "non-itemizers and self-employed"
    assert repair_passage("tax-ex-\nempt bond") == "tax-exempt bond"


def test_leading_t_and_y_join_and_other_capitals_stay() -> None:
    raw = (
        "T axpayers and Y ou may file. A flat fee. I am here. S corporation. Section B of the form."
    )
    assert repair_passage(raw) == (
        "Taxpayers and You may file. A flat fee. I am here. S corporation. Section B of the form."
    )


def test_citation_and_prompt_repair_text_already_in_the_index() -> None:
    hit = Hit(
        id="chunk",
        document_id="doc",
        title="2025 Publication 17",
        source="p17.pdf",
        section="What's New",
        page=3,
        year="2025",
        content="your digi- tal wallet. T axpayers. Y ou may. non -itemizers.",
        score=1.0,
    )
    assert _citation(hit)["snippet"] == "your digital wallet. Taxpayers. You may. non-itemizers."
    assert "your digital wallet. Taxpayers. You may. non-itemizers." in render_context([hit])


def test_publication_17_page_joins_the_reported_splits() -> None:
    reader = PdfReader(ROOT / "sample-docs" / "irs" / "p17.pdf")
    text = repair_passage(reader.pages[2].extract_text() or "")
    assert "digital wallet" in text
    assert "non-itemizers" in text
    assert "Taxpayers who made" in text
    assert "You may" in text
    assert "digi-" not in text
    assert "non -itemizers" not in text
    assert "T ax" not in text
    assert "Y ou" not in text
