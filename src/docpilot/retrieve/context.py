"""Turn raw search hits into a bounded, stable context list."""

from dataclasses import dataclass, replace

from docpilot.ingest.chunk import estimate_tokens


@dataclass(frozen=True)
class Hit:
    id: str
    document_id: str
    title: str
    source: str
    section: str
    page: int
    year: str | None
    content: str
    score: float


def select_hits(hits: list[Hit], *, token_budget: int) -> list[Hit]:
    """Drop duplicates, order by score, and stop at the token budget.

    A higher score wins. Equal scores keep the smaller chunk id so the order
    does not change between calls. The last passage is shortened when it is
    the one that would cross the budget.
    """
    if token_budget < 1:
        return []
    selected: list[Hit] = []
    used = 0
    for hit in _deduped(hits):
        cost = estimate_tokens(hit.content)
        if cost < 1:
            continue
        if used + cost <= token_budget:
            selected.append(hit)
            used += cost
            continue
        remaining = token_budget - used
        clipped = _truncate(hit.content, remaining)
        if estimate_tokens(clipped) > 0:
            selected.append(replace(hit, content=clipped))
        break
    return selected


def _deduped(hits: list[Hit]) -> list[Hit]:
    by_id: dict[str, Hit] = {}
    for hit in hits:
        current = by_id.get(hit.id)
        if current is None or _outranks(hit, current):
            by_id[hit.id] = hit
    by_content: dict[str, Hit] = {}
    for hit in by_id.values():
        key = _content_key(hit.content)
        current = by_content.get(key)
        if current is None or _outranks(hit, current):
            by_content[key] = hit
    return sorted(by_content.values(), key=lambda hit: (-hit.score, hit.id))


def _outranks(candidate: Hit, current: Hit) -> bool:
    if candidate.score != current.score:
        return candidate.score > current.score
    return candidate.id < current.id


def _content_key(content: str) -> str:
    return " ".join(content.split()).casefold()


def _truncate(text: str, token_budget: int) -> str:
    if token_budget < 1:
        return ""
    if estimate_tokens(text) <= token_budget:
        return text
    limit = max(0, token_budget * 4 - 3)
    clipped = text[:limit]
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    while clipped and estimate_tokens(clipped) > token_budget:
        clipped = clipped[: max(0, len(clipped) - 4)]
    return clipped.strip()
