from pathlib import Path

from fastapi.testclient import TestClient

from docpilot.ask.service import Asker
from docpilot.config import Settings
from docpilot.documents.models import DocumentRecord
from docpilot.documents.store import MemoryDocumentStore
from docpilot.main import create_app
from docpilot.retrieve.context import Hit


def _hit() -> Hit:
    return Hit(
        id="chunk",
        document_id="doc",
        title="Pub 587",
        source="p587.pdf",
        section="Home office",
        page=4,
        year="2025",
        content="Exclusive use is required for the deduction.",
        score=1.0,
    )


class HitRetriever:
    def retrieve(self, question: str) -> list[Hit]:
        assert question == "home office"
        return [_hit()]


class EmptyRetriever:
    def retrieve(self, question: str) -> list[Hit]:
        del question
        return []


class ScriptedChat:
    def __init__(self, text: str, tokens: int = 12) -> None:
        self.text = text
        self.tokens = tokens
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> tuple[str, int]:
        self.prompts.append(prompt)
        return self.text, self.tokens


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "admin_password_hash": "hash",
        "session_secret": "secret",
        "cookie_secure": False,
        "storage_account_url": "",
        "storage_container": "sources",
        "storage_connection_string": "",
        "data_dir": tmp_path,
        "max_question_chars": 40,
        "ask_rate_limit": 2,
        "ask_rate_window_seconds": 60,
        "daily_token_cap": 20,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _client(tmp_path: Path, asker: Asker, **overrides: object) -> TestClient:
    application = create_app(_settings(tmp_path, **overrides), MemoryDocumentStore(), asker)
    return TestClient(application)


def test_ask_returns_grounded_citations(tmp_path: Path) -> None:
    chat = ScriptedChat("The deduction requires exclusive use.")
    client = _client(tmp_path, Asker(HitRetriever(), chat))
    response = client.post("/ask", json={"question": " home office "})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "The deduction requires exclusive use."
    assert body["citations"] == [
        {
            "document_id": "doc",
            "doc": "Pub 587",
            "section": "Home office",
            "page": 4,
            "snippet": "Exclusive use is required for the deduction.",
            "year": "2025",
        }
    ]
    assert body["trace_id"]
    assert "home office" in chat.prompts[0]
    assert "not found in the documents" in chat.prompts[0]


def test_unsupported_question_is_not_found(tmp_path: Path) -> None:
    chat = ScriptedChat("Not found in the documents.")
    client = _client(tmp_path, Asker(HitRetriever(), chat))
    response = client.post("/ask", json={"question": "home office"})
    assert response.status_code == 200
    assert response.json()["answer"] == "not found in the documents"
    assert response.json()["citations"] == []


def test_no_hits_skips_the_model(tmp_path: Path) -> None:
    chat = ScriptedChat("should not be called")
    client = _client(tmp_path, Asker(EmptyRetriever(), chat))
    response = client.post("/ask", json={"question": "home office"})
    assert response.json()["answer"] == "not found in the documents"
    assert chat.prompts == []


def test_question_too_long_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path, Asker(EmptyRetriever(), ScriptedChat("x")))
    response = client.post("/ask", json={"question": "a" * 41})
    assert response.status_code == 400
    assert response.json()["error"] == "question_too_long"


def test_rate_limit_blocks_the_third_question(tmp_path: Path) -> None:
    client = _client(tmp_path, Asker(EmptyRetriever(), ScriptedChat("x")))
    assert client.post("/ask", json={"question": "one"}).status_code == 200
    assert client.post("/ask", json={"question": "two"}).status_code == 200
    blocked = client.post("/ask", json={"question": "three"})
    assert blocked.status_code == 429
    assert blocked.json()["error"] == "rate_limited"


def test_daily_token_cap_returns_the_demo_message(tmp_path: Path) -> None:
    chat = ScriptedChat("Exclusive use.", tokens=20)
    client = _client(tmp_path, Asker(HitRetriever(), chat))
    first = client.post("/ask", json={"question": "home office"})
    assert first.status_code == 200
    second = client.post("/ask", json={"question": "home office"})
    assert second.status_code == 429
    assert second.json()["answer"] == "demo limit reached"


def test_citation_line_links_to_the_source_page(tmp_path: Path) -> None:
    text = _client(tmp_path, Asker(None, None)).get("/").text
    assert "citation.year" not in text
    assert "/sources/${encodeURIComponent(documentId)}" in text
    assert "#page=" in text
    assert 'target = "_blank"' in text
    assert 'rel = "noopener"' in text
    assert "snippet.textContent" in text


def test_source_pdf_comes_from_the_app(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    document_id = "a" * 32
    store.put_record(
        DocumentRecord(
            id=document_id,
            title="2025 Publication 502",
            source="p502.pdf",
            year="2025",
            pages=27,
            chunk_count=1,
            index_bytes=10,
            source_bytes=8,
            ingested_at=None,
            status="ready",
        )
    )
    store.put_pdf(document_id, b"%PDF-1.4 test")
    client = TestClient(create_app(_settings(tmp_path), store, Asker(None, None)))
    response = client.get(f"/sources/{document_id}")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 test"
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"] == 'inline; filename="p502.pdf"'
    assert "blob.core" not in response.text
    assert client.get("/sources/missing-document").status_code == 404
    assert client.get("/sources/../secret").status_code == 404
    store.delete_pdf(document_id)
    assert client.get(f"/sources/{document_id}").status_code == 404


def test_answer_markdown_renders_and_citations_are_smaller(tmp_path: Path) -> None:
    page = _client(tmp_path, Asker(None, None)).get("/")
    text = page.text
    assert "function renderAnswer" in text
    assert 'text.startsWith("**"' in text
    assert "answer.textContent = body.answer" not in text
    assert "font-size: 0.88em" in text
    citation = text.index(".citation {")
    assert "font-size: 0.88em" in text[citation:citation + 180]


def test_ask_box_is_disabled_while_loading(tmp_path: Path) -> None:
    text = _client(tmp_path, Asker(None, None)).get("/").text
    assert 'id="ask-button"' in text
    assert "question.disabled = true" in text
    assert "askButton.disabled = true" in text
    submit = text.index('addEventListener("submit"')
    handler = text[submit:]
    assert "if (askButton.disabled) return;" in handler
    assert handler.index("if (askButton.disabled) return;") < handler.index("question.disabled = true")
    assert handler.index("question.disabled = true") < handler.index("await fetch")
    assert "question.disabled = false" in handler
    assert "askButton.disabled = false" in handler
    assert handler.index("finally") < handler.index("question.disabled = false")
    assert 'id="ask-spinner" class="spinner hidden"' in text
    assert handler.index('spinner.classList.remove("hidden")') < handler.index("await fetch")
    assert handler.index("finally") < handler.index('spinner.classList.add("hidden")')


def test_enter_submits_the_ask_box(tmp_path: Path) -> None:
    client = _client(tmp_path, Asker(None, None))
    page = client.get("/")
    assert 'addEventListener("keydown"' in page.text
    assert 'event.key !== "Enter"' in page.text
    assert "event.ctrlKey" in page.text
    assert 'setRangeText("\\n"' in page.text
    assert "form.requestSubmit()" in page.text


def test_home_page_credits_the_designer(tmp_path: Path) -> None:
    page = _client(tmp_path, Asker(None, None)).get("/")
    assert page.status_code == 200
    text = page.text
    credit = text.index('class="site-credit"')
    assert credit > text.index('id="result"')
    assert "Designed &amp; built by" in text
    assert 'href="https://www.hmeyer.com" target="_blank" rel="noopener">Henrique Meyer</a>' in text
    assert (
        'href="https://github.com/henrique-meyer/docpilot" target="_blank" rel="noopener"'
        in text
    )
    assert "Source on GitHub" in text
    assert "©" not in text
    assert "Cursor" not in text


def test_documents_page_lists_uploads_and_reuses_a_named_tab(tmp_path: Path) -> None:
    store = MemoryDocumentStore()
    first = "a" * 32
    second = "b" * 32
    store.put_record(
        DocumentRecord(
            id=first,
            title="2025 Publication 502",
            source="p502.pdf",
            year="2025",
            pages=27,
            chunk_count=1,
            index_bytes=10,
            source_bytes=8,
            ingested_at=None,
            status="ready",
        )
    )
    store.put_record(
        DocumentRecord(
            id=second,
            title="",
            source="notes.pdf",
            year=None,
            pages=1,
            chunk_count=0,
            index_bytes=0,
            source_bytes=4,
            ingested_at=None,
            status="ready",
        )
    )
    client = TestClient(create_app(_settings(tmp_path), store, Asker(None, None)))
    client.app.state.documents.save_summary(
        "IRS Publications for Preparing 2025 Returns",
        "These documents provide guidance for filing 2025 personal tax returns.",
        ("Do dead people have to file taxes?", "Second question", "Third question"),
    )
    home = client.get("/")
    assert 'href="/documents">View Documents</a>' in home.text
    assert home.text.index("These documents provide guidance") < home.text.index("View Documents")
    page = client.get("/documents")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    text = page.text
    assert "<h1>IRS Publications for Preparing 2025 Returns</h1>" in text
    assert "These documents provide guidance for filing 2025 personal tax returns." in text
    assert "Sample questions" not in text
    assert "<li>" not in text.split('class="document-list"')[0]
    assert 'href="/">Ask about these documents</a>' in text
    assert text.index("Ask about these documents") < text.index('class="document-list"')
    assert f'href="/sources/{first}#page=1" target="document_{first}">2025 Publication 502</a>' in text
    assert f'href="/sources/{second}#page=1" target="document_{second}">notes.pdf</a>' in text
    assert "2025</a>" not in text
    assert "blob.core.windows.net" not in text


def test_home_page_states_the_disclaimer(tmp_path: Path) -> None:
    client = _client(tmp_path, Asker(None, None))
    page = client.get("/")
    assert page.status_code == 200
    assert "informational only" in page.text
    assert "selected set for demonstration" in page.text
    assert "not a complete library" in page.text
    assert "definitive guide" in page.text
    assert 'href="/assets/logo.svg"' in page.text
    assert 'class="catalog-summary"' not in page.text
    assert client.get("/health").json() == {"status": "ok"}


def test_home_page_shows_the_catalog_summary_above_the_ask_box(tmp_path: Path) -> None:
    client = _client(tmp_path, Asker(None, None))
    client.app.state.documents.save_summary(
        "IRS Publications for use in preparing 2025 returns",
        "These documents provide guidance for filing 2025 personal tax returns.",
        ("Do dead people have to file taxes?", "", "What if my wife doesn't live in the US?"),
    )
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    text = page.text
    assert text.index('class="catalog-summary"') < text.index('id="question"')
    assert "<h1>IRS Publications for use in preparing 2025 returns</h1>" in text
    assert "These documents provide guidance for filing 2025 personal tax returns." in text
    assert "<li>Do dead people have to file taxes?</li>" in text
    assert "What if my wife doesn" in text
    assert text.count("<li>") == 2

    client.app.state.documents.save_summary("<script>", "a & b", ("", "", ""))
    escaped = client.get("/").text
    assert "<h1>&lt;script&gt;</h1>" in escaped
    assert "<h1><script>" not in escaped
    assert "<p>a &amp; b <a href=\"/documents\">View Documents</a></p>" in escaped


class BrokenChat:
    def complete(self, prompt: str) -> tuple[str, int]:
        raise RuntimeError(prompt)


def test_model_outage_is_not_an_empty_corpus(tmp_path: Path) -> None:
    client = _client(tmp_path, Asker(HitRetriever(), BrokenChat()))
    response = client.post("/ask", json={"question": "home office"})
    assert response.status_code == 503
    assert response.json()["error"] == "unavailable"
