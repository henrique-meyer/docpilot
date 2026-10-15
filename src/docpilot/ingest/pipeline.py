"""Chunk a PDF and upsert it. Embeddings and search are injected so tests stay offline."""

from dataclasses import dataclass
from typing import Protocol

from docpilot.documents.errors import DocumentError
from docpilot.ingest.chunk import DEFAULT_CHUNK_TOKENS, DEFAULT_OVERLAP_TOKENS, Chunk, chunk_pages
from docpilot.ingest.pdftext import read_pages

VECTOR_DIMENSIONS = 512


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChunkIndex(Protocol):
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    def delete_except(self, document_id: str, keep: set[str]) -> None: ...

    def clear(self) -> None: ...


@dataclass(frozen=True)
class IngestResult:
    pages: int
    chunk_count: int
    index_bytes: int


class Ingestor:
    def __init__(
        self,
        embedder: Embedder | None,
        index: ChunkIndex | None,
        *,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        self.embedder = embedder
        self.index = index
        self.chunk_tokens = chunk_tokens
        self.overlap_tokens = overlap_tokens

    def ingest(
        self,
        data: bytes,
        *,
        document_id: str,
        title: str,
        source: str,
        year: str | None,
    ) -> IngestResult:
        pages = read_pages(data)
        chunks = chunk_pages(
            pages,
            document_id=document_id,
            title=title,
            source=source,
            year=year,
            chunk_tokens=self.chunk_tokens,
            overlap_tokens=self.overlap_tokens,
        )
        if self.index is not None:
            vectors = self._vectors(chunks)
            self.index.upsert(chunks, vectors)
            self.index.delete_except(document_id, {chunk.id for chunk in chunks})
        return IngestResult(
            pages=len(pages),
            chunk_count=len(chunks),
            index_bytes=sum(chunk.index_bytes() for chunk in chunks),
        )

    def delete(self, document_id: str) -> None:
        if self.index is not None:
            self.index.delete_except(document_id, set())

    def clear(self) -> None:
        if self.index is not None:
            self.index.clear()

    def _vectors(self, chunks: list[Chunk]) -> list[list[float]]:
        if not chunks:
            return []
        if self.embedder is None:
            return [[0.0] * VECTOR_DIMENSIONS for _ in chunks]
        vectors = self.embedder.embed([chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise DocumentError("not_pdf")
        return vectors


class MemoryIndex:
    """Process-local stand-in used when Azure AI Search is not configured."""

    def __init__(self) -> None:
        self.docs: dict[str, Chunk] = {}

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        del vectors
        for chunk in chunks:
            self.docs[chunk.id] = chunk

    def delete_except(self, document_id: str, keep: set[str]) -> None:
        for chunk_id, chunk in list(self.docs.items()):
            if chunk.document_id == document_id and chunk_id not in keep:
                del self.docs[chunk_id]

    def clear(self) -> None:
        self.docs.clear()
