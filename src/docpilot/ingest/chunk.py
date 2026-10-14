"""Split extracted PDF text into stable chunks.

Token counts are an approximation: one token is about four characters. Chunk
size and overlap stay configurable so the same function serves tests and the
live index.
"""

from dataclasses import dataclass

DEFAULT_CHUNK_TOKENS = 700
DEFAULT_OVERLAP_TOKENS = 80


@dataclass(frozen=True)
class PageText:
    page: int
    section: str
    text: str


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    title: str
    source: str
    year: str | None
    section: str
    page: int
    content: str

    def index_bytes(self) -> int:
        return len(self.content.encode("utf-8")) + (512 * 4)


def estimate_tokens(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    return max(1, (len(stripped) + 3) // 4)


def chunk_pages(
    pages: list[PageText],
    *,
    document_id: str,
    title: str,
    source: str,
    year: str | None,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    if chunk_tokens < 1:
        raise ValueError("chunk_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens must be smaller than chunk_tokens")
    chunks: list[Chunk] = []
    for page in pages:
        parts = _split(page.text, chunk_tokens, overlap_tokens)
        for ordinal, content in enumerate(parts):
            chunks.append(
                Chunk(
                    id=f"{document_id}-{page.page:04d}-{ordinal:04d}",
                    document_id=document_id,
                    title=title,
                    source=source,
                    year=year,
                    section=page.section,
                    page=page.page,
                    content=content,
                )
            )
    return chunks


def _split(text: str, chunk_tokens: int, overlap_tokens: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for word in words:
        word_tokens = max(1, (len(word) + 3) // 4)
        if current and current_tokens + word_tokens > chunk_tokens:
            chunks.append(" ".join(current))
            current, current_tokens = _overlap(current, overlap_tokens)
        current.append(word)
        current_tokens += word_tokens
    if current:
        chunks.append(" ".join(current))
    return chunks


def _overlap(words: list[str], overlap_tokens: int) -> tuple[list[str], int]:
    kept: list[str] = []
    tokens = 0
    for word in reversed(words):
        word_tokens = max(1, (len(word) + 3) // 4)
        if tokens + word_tokens > overlap_tokens:
            break
        kept.append(word)
        tokens += word_tokens
    kept.reverse()
    return kept, tokens
