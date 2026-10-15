"""Hybrid retrieval. Embeddings and search are injected so tests stay offline."""

from typing import Protocol

from docpilot.config import Settings
from docpilot.observe import span
from docpilot.retrieve.context import Hit, select_hits


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Searcher(Protocol):
    def search(self, question: str, vector: list[float], top_k: int) -> list[Hit]: ...


class Retriever:
    def __init__(
        self,
        embedder: Embedder,
        searcher: Searcher,
        *,
        top_k: int,
        token_budget: int,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        self.embedder = embedder
        self.searcher = searcher
        self.top_k = top_k
        self.token_budget = token_budget

    def retrieve(self, question: str) -> list[Hit]:
        with span("retrieval") as current:
            text = question.strip()
            if not text:
                current.set_attribute("hits", 0)
                return []
            vectors = self.embedder.embed([text])
            if not vectors or not vectors[0]:
                current.set_attribute("hits", 0)
                return []
            raw = self.searcher.search(text, vectors[0], self.top_k)
            selected = select_hits(raw, token_budget=self.token_budget)
            current.set_attribute("hits", len(selected))
            return selected


def build_retriever(settings: Settings) -> Retriever | None:
    """Return a live retriever, or None when OpenAI or Search is not configured."""
    if not settings.openai_endpoint or not settings.search_endpoint:
        return None
    from docpilot.ingest.azure import AzureEmbedder
    from docpilot.retrieve.azure import AzureHybridSearch

    return Retriever(
        AzureEmbedder(settings.openai_endpoint, settings.embedding_deployment),
        AzureHybridSearch(settings.search_endpoint, settings.search_index),
        top_k=settings.retrieval_top_k,
        token_budget=settings.retrieval_token_budget,
    )
