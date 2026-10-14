"""Shared ingestion path for admin uploads and the seed command."""

from docpilot.config import Settings
from docpilot.ingest.pipeline import Ingestor, MemoryIndex

__all__ = ["Ingestor", "build_ingestor"]


def build_ingestor(settings: Settings) -> Ingestor:
    if settings.openai_endpoint and settings.search_endpoint:
        from docpilot.ingest.azure import AzureChunkIndex, AzureEmbedder

        return Ingestor(
            AzureEmbedder(settings.openai_endpoint, settings.embedding_deployment),
            AzureChunkIndex(settings.search_endpoint, settings.search_index),
            chunk_tokens=settings.chunk_tokens,
            overlap_tokens=settings.overlap_tokens,
        )
    return Ingestor(
        None,
        MemoryIndex(),
        chunk_tokens=settings.chunk_tokens,
        overlap_tokens=settings.overlap_tokens,
    )
