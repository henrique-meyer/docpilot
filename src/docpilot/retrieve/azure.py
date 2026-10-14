"""Azure AI Search hybrid query. Semantic ranking is skipped when the call fails."""

from collections.abc import Mapping

from docpilot.ingest.azure import SEMANTIC_CONFIG
from docpilot.retrieve.context import Hit

_SELECT = ["id", "content", "document_id", "title", "source", "section", "page", "year"]


class AzureHybridSearch:
    def __init__(self, endpoint: str, index_name: str, client: object | None = None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._index_name = index_name
        self._client_override = client

    def search(self, question: str, vector: list[float], top_k: int) -> list[Hit]:
        try:
            return self._query(question, vector, top_k, semantic=True)
        except Exception:  # noqa: BLE001
            # A missing semantic config or an exhausted free allowance must
            # still return hybrid results.
            return self._query(question, vector, top_k, semantic=False)

    def _query(self, question: str, vector: list[float], top_k: int, *, semantic: bool) -> list[Hit]:
        from azure.search.documents.models import VectorizedQuery

        kwargs: dict[str, object] = {
            "search_text": question,
            "vector_queries": [
                VectorizedQuery(
                    vector=vector,
                    k_nearest_neighbors=top_k,
                    fields="content_vector",
                )
            ],
            "top": top_k,
            "select": _SELECT,
        }
        if semantic:
            kwargs["query_type"] = "semantic"
            kwargs["semantic_configuration_name"] = SEMANTIC_CONFIG
        results = self._client().search(**kwargs)  # type: ignore[attr-defined]
        return [hit_from_result(item) for item in results]

    def _client(self) -> object:
        if self._client_override is not None:
            return self._client_override
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient

        return SearchClient(self._endpoint, self._index_name, DefaultAzureCredential())


def hit_from_result(item: Mapping[str, object]) -> Hit:
    year = str(item.get("year") or "")
    reranker = item.get("@search.reranker_score")
    if isinstance(reranker, int | float):
        score = float(reranker)
    else:
        raw = item.get("@search.score", 0.0)
        score = float(raw) if isinstance(raw, int | float) else 0.0
    page = item.get("page", 0)
    if isinstance(page, bool) or not isinstance(page, int | float | str):
        page_number = 0
    else:
        page_number = int(page)
    return Hit(
        id=str(item.get("id") or ""),
        document_id=str(item.get("document_id") or ""),
        title=str(item.get("title") or ""),
        source=str(item.get("source") or ""),
        section=str(item.get("section") or ""),
        page=page_number,
        year=year or None,
        content=str(item.get("content") or ""),
        score=score,
    )
