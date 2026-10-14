"""Azure OpenAI embeddings and Azure AI Search upserts.

The container app uses its managed identity. Tests never construct this module's clients.
"""

from docpilot.ingest.chunk import Chunk
from docpilot.ingest.pipeline import VECTOR_DIMENSIONS


class AzureEmbedder:
    def __init__(self, endpoint: str, deployment: str) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._deployment = deployment
        self._client: object | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._openai()
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 16):
            batch = texts[start : start + 16]
            response = client.embeddings.create(  # type: ignore[attr-defined]
                model=self._deployment,
                input=batch,
                dimensions=VECTOR_DIMENSIONS,
            )
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in ordered)
        return vectors

    def _openai(self) -> object:
        if self._client is not None:
            return self._client
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from openai import AzureOpenAI

        token = get_bearer_token_provider(
            DefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        self._client = AzureOpenAI(
            azure_endpoint=self._endpoint,
            azure_ad_token_provider=token,
            api_version="2024-10-21",
        )
        return self._client


class AzureChunkIndex:
    def __init__(self, endpoint: str, index_name: str) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._index_name = index_name
        self._ready = False

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        self._ensure_index()
        documents: list[dict[str, object]] = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            documents.append(
                {
                    "id": chunk.id,
                    "content": chunk.content,
                    "content_vector": vector,
                    "document_id": chunk.document_id,
                    "title": chunk.title,
                    "source": chunk.source,
                    "section": chunk.section,
                    "page": chunk.page,
                    "year": chunk.year or "",
                }
            )
        client = self._documents()
        client.upload_documents(documents)  # type: ignore[attr-defined]

    def delete_except(self, document_id: str, keep: set[str]) -> None:
        self._ensure_index()
        client = self._documents()
        results = client.search(  # type: ignore[attr-defined]
            search_text="*",
            filter=f"document_id eq '{_odata(document_id)}'",
            select=["id"],
        )
        stale = [{"id": item["id"]} for item in results if item["id"] not in keep]
        if stale:
            client.delete_documents(stale)  # type: ignore[attr-defined]

    def _ensure_index(self) -> None:
        if self._ready:
            return
        from azure.core.exceptions import ResourceNotFoundError
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            SearchField,
            SearchFieldDataType,
            SearchIndex,
            VectorSearch,
            VectorSearchProfile,
        )

        client = self._indexes()
        fields = [
            SearchField(name="id", type=SearchFieldDataType.String, key=True),
            SearchField(name="content", type=SearchFieldDataType.String, searchable=True),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),  # type: ignore[operator]
                searchable=True,
                vector_search_dimensions=VECTOR_DIMENSIONS,
                vector_search_profile_name="docpilot-hnsw",
            ),
            SearchField(
                name="document_id",
                type=SearchFieldDataType.String,
                filterable=True,
            ),
            SearchField(
                name="title",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=True,
            ),
            SearchField(name="source", type=SearchFieldDataType.String, filterable=True),
            SearchField(
                name="section",
                type=SearchFieldDataType.String,
                searchable=True,
                filterable=True,
            ),
            SearchField(name="page", type=SearchFieldDataType.Int32, filterable=True),
            SearchField(name="year", type=SearchFieldDataType.String, filterable=True),
        ]
        index = SearchIndex(
            name=self._index_name,
            fields=fields,
            vector_search=VectorSearch(
                algorithms=[HnswAlgorithmConfiguration(name="docpilot-hnsw-algo")],
                profiles=[
                    VectorSearchProfile(
                        name="docpilot-hnsw",
                        algorithm_configuration_name="docpilot-hnsw-algo",
                    )
                ],
            ),
        )
        try:
            client.get_index(self._index_name)  # type: ignore[attr-defined]
        except ResourceNotFoundError:
            client.create_index(index)  # type: ignore[attr-defined]
        self._ready = True

    def _indexes(self) -> object:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents.indexes import SearchIndexClient

        return SearchIndexClient(self._endpoint, DefaultAzureCredential())

    def _documents(self) -> object:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient

        return SearchClient(self._endpoint, self._index_name, DefaultAzureCredential())


def _odata(value: str) -> str:
    return value.replace("'", "''")
