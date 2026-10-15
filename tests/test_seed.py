from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter

from docpilot.config import Settings
from docpilot.documents.service import DocumentService
from docpilot.documents.store import MemoryDocumentStore
from docpilot.ingest.pipeline import Ingestor, MemoryIndex
from docpilot.seed.load import SeedError, seed_set


def _pdf(title: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": title})
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _service(tmp_path: Path) -> DocumentService:
    settings = Settings(
        admin_password_hash="",
        session_secret="",
        cookie_secure=False,
        storage_account_url="",
        storage_container="sources",
        storage_connection_string="",
        data_dir=tmp_path,
    )
    return DocumentService(MemoryDocumentStore(), settings, ingestor=Ingestor(None, MemoryIndex()))


def test_seed_uses_the_upload_path_and_skips_ready_files(tmp_path: Path) -> None:
    sample = tmp_path / "sample-docs" / "irs"
    sample.mkdir(parents=True)
    (sample / "guide.pdf").write_bytes(_pdf("2025 Visitor guide"))
    service = _service(tmp_path)
    added, skipped = seed_set(tmp_path / "sample-docs", "irs", service)
    assert added == ["guide.pdf"]
    assert skipped == []
    record = service.list_documents()[0]
    assert record.title == "2025 Visitor guide"
    assert record.year == "2025"
    assert record.status == "ready"
    assert service.list_audit()[0].action == "upload"
    again, skipped_again = seed_set(tmp_path / "sample-docs", "irs", service)
    assert again == []
    assert skipped_again == ["guide.pdf"]
    assert len(service.list_documents()) == 1


def test_fetch_reads_the_catalog_and_skips_a_saved_pdf(tmp_path: Path) -> None:
    from docpilot.seed.download import fetch_set

    sample = tmp_path / "irs"
    sample.mkdir()
    (sample / "SOURCES.md").write_text(
        "- guide.pdf: https://example.gov/guide.pdf (10 KB)\n",
        encoding="utf-8",
    )
    (sample / "guide.pdf").write_bytes(b"%PDF-already")
    downloaded, skipped = fetch_set(tmp_path, "irs", opener=lambda url: b"%PDF-new")
    assert downloaded == []
    assert skipped == ["guide.pdf"]
    (sample / "guide.pdf").unlink()
    downloaded, skipped = fetch_set(tmp_path, "irs", opener=lambda url: b"%PDF-1.4")
    assert downloaded == ["guide.pdf"]
    assert skipped == []
    assert (sample / "guide.pdf").read_bytes().startswith(b"%PDF-")


def test_missing_set_is_an_error(tmp_path: Path) -> None:
    try:
        seed_set(tmp_path, "missing", _service(tmp_path))
    except SeedError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected SeedError")
