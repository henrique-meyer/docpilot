from pathlib import Path

from docpilot.config import Settings
from docpilot.retrieve.azure import AzureHybridSearch, hit_from_result
from docpilot.retrieve.context import Hit, select_hits
from docpilot.retrieve.prompt import PROMPT_VERSION, build_prompt
from docpilot.retrieve.retriever import Retriever, build_retriever


def _hit(hit_id: str, content: str, score: float, page: int = 1) -> Hit:
    return Hit(
        id=hit_id,
        document_id="doc",
        title="Pub 587",
        source="p587.pdf",
        section="Home office",
        page=page,
        year="2025",
        content=content,
        score=score,
    )


def test_select_hits_orders_by_score_then_id() -> None:
    selected = select_hits(
        [
            _hit("b", "beta", 2.0),
            _hit("c", "gamma", 2.0),
            _hit("a", "alpha", 1.0),
        ],
        token_budget=100,
    )
    assert [hit.id for hit in selected] == ["b", "c", "a"]


def test_select_hits_keeps_the_stronger_duplicate() -> None:
    selected = select_hits(
        [
            _hit("low", "Exclusive use is required.", 0.4),
            _hit("high", "  exclusive   use is required. ", 1.5),
            _hit("high", "  exclusive   use is required. ", 0.2),
        ],
        token_budget=100,
    )
    assert [hit.id for hit in selected] == ["high"]
    assert selected[0].score == 1.5


def test_select_hits_stops_at_the_token_budget() -> None:
    selected = select_hits(
        [
            _hit("one", "aaaa " * 20, 3.0),
            _hit("two", "bbbb " * 20, 2.0),
            _hit("three", "cccc " * 20, 1.0),
        ],
        token_budget=30,
    )
    assert [hit.id for hit in selected] == ["one", "two"]
    assert selected[1].content.endswith("bbbb")


def test_select_hits_shortens_a_passage_that_crosses_the_budget() -> None:
    selected = select_hits([_hit("only", "alpha " * 40, 1.0)], token_budget=10)
    assert len(selected) == 1
    assert selected[0].id == "only"
    assert len(selected[0].content) < len("alpha " * 40)


def test_prompt_keeps_braces_and_states_the_refusal() -> None:
    prompt = build_prompt(
        " What is the {context} rule? ",
        [_hit("one", "Use only {question} from the text.", 1.0, page=4)],
    )
    assert PROMPT_VERSION == "answer_v1"
    assert "What is the {context} rule?" in prompt
    assert "Use only {question} from the text." in prompt
    assert "not found in the documents" in prompt
    assert "[1] Pub 587 | Home office | p. 4 | 2025" in prompt


def test_empty_context_still_builds_a_prompt() -> None:
    prompt = build_prompt("Where is the park?", [])
    assert "(none)" in prompt


def test_retriever_embeds_the_question_and_applies_top_k() -> None:
    class Embed:
        def embed(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["home office"]
            return [[0.1, 0.2]]

    class Search:
        def __init__(self) -> None:
            self.top_k = 0

        def search(self, question: str, vector: list[float], top_k: int) -> list[Hit]:
            assert question == "home office"
            assert vector == [0.1, 0.2]
            self.top_k = top_k
            return [_hit("one", "Exclusive use.", 1.0)]

    search = Search()
    hits = Retriever(Embed(), search, top_k=3, token_budget=100).retrieve(" home office ")
    assert search.top_k == 3
    assert [hit.id for hit in hits] == ["one"]


def test_semantic_failure_falls_back_to_hybrid() -> None:
    class Client:
        def __init__(self) -> None:
            self.modes: list[str] = []

        def search(self, **kwargs: object) -> list[dict[str, object]]:
            mode = str(kwargs.get("query_type") or "hybrid")
            self.modes.append(mode)
            if mode == "semantic":
                raise RuntimeError("semantic allowance exhausted")
            return [
                {
                    "id": "chunk",
                    "content": "Exclusive use is required.",
                    "document_id": "doc",
                    "title": "Pub 587",
                    "source": "p587.pdf",
                    "section": "Home office",
                    "page": 3,
                    "year": "",
                    "@search.score": 0.4,
                }
            ]

    client = Client()
    hits = AzureHybridSearch("https://search.example", "docpilot", client).search(
        "home office",
        [0.1, 0.2],
        4,
    )
    assert client.modes == ["semantic", "hybrid"]
    assert hits[0].id == "chunk"
    assert hits[0].year is None
    assert hits[0].page == 3
    assert hits[0].score == 0.4


def test_reranker_score_wins_when_semantic_succeeds() -> None:
    parsed = hit_from_result(
        {
            "id": "chunk",
            "content": "text",
            "@search.score": 0.2,
            "@search.reranker_score": 1.8,
        }
    )
    assert parsed.score == 1.8


def test_build_retriever_is_absent_without_endpoints() -> None:
    settings = Settings(
        admin_password_hash="",
        session_secret="",
        cookie_secure=False,
        storage_account_url="",
        storage_container="sources",
        storage_connection_string="",
        data_dir=Path(".data"),
    )
    assert build_retriever(settings) is None
