"""Tools the agent may call. Every result comes from the retriever."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from docpilot.ask.service import PassageSource, _citation
from docpilot.retrieve.context import Hit

LIMIT_REACHED = "tool call limit reached"
RESULT_CHARS = 500


class ToolKit:
    """Records each call and stops once the cap is reached."""

    def __init__(self, retriever: PassageSource | None, max_calls: int) -> None:
        self.retriever = retriever
        self.max_calls = max_calls
        self.calls: list[dict[str, object]] = []
        self.hits: list[Hit] = []
        self._seen: set[str] = set()

    def search_documents(
        self, query: Annotated[str, "What to look up in the uploaded documents."]
    ) -> str:
        """Search the uploaded documents and return matching passages."""
        return self._run("search_documents", {"query": query}, lambda: self._search(query))

    def get_section(
        self,
        document: Annotated[str, "Document title, or part of it."],
        section: Annotated[str, "Section heading to open."],
    ) -> str:
        """Open one section of one uploaded document."""
        return self._run(
            "get_section",
            {"document": document, "section": section},
            lambda: self._section(document, section),
        )

    def compare_documents(
        self, topic: Annotated[str, "Topic to compare across the uploaded documents."]
    ) -> str:
        """Say what each uploaded document says about one topic."""
        return self._run(
            "compare_documents",
            {"topic": topic},
            lambda: self._compare(topic),
        )

    def citations(self) -> list[dict[str, object]]:
        return [_citation(hit) for hit in self.hits]

    def _run(self, name: str, arguments: dict[str, str], action: Callable[[], str]) -> str:
        if len(self.calls) >= self.max_calls:
            return self._record(name, arguments, LIMIT_REACHED)
        return self._record(name, arguments, action())

    def _record(self, name: str, arguments: dict[str, str], result: str) -> str:
        shown = result if len(result) <= RESULT_CHARS else result[: RESULT_CHARS - 1].rstrip() + "…"
        self.calls.append({"name": name, "arguments": arguments, "result": shown})
        return result

    def _search(self, query: str) -> str:
        hits = self._retrieve(query)
        self._remember(hits)
        if not hits:
            return "No matching passages."
        return _format(hits)

    def _section(self, document: str, section: str) -> str:
        hits = [
            hit
            for hit in self._retrieve(f"{document} {section}".strip())
            if _matches_document(hit, document) and _matches_section(hit, section)
        ]
        self._remember(hits)
        if not hits:
            return "That section was not found."
        return _format(hits)

    def _compare(self, topic: str) -> str:
        hits = self._retrieve(topic)
        self._remember(hits)
        if not hits:
            return "No matching passages."
        grouped: dict[str, list[Hit]] = {}
        for hit in hits:
            grouped.setdefault(hit.title, []).append(hit)
        blocks = [f"{title}\n{_format(group)}" for title, group in grouped.items()]
        return "\n\n".join(blocks)

    def _retrieve(self, query: str) -> list[Hit]:
        if self.retriever is None:
            return []
        return self.retriever.retrieve(query)

    def _remember(self, hits: list[Hit]) -> None:
        for hit in hits:
            if hit.id in self._seen:
                continue
            self._seen.add(hit.id)
            self.hits.append(hit)


def _matches_document(hit: Hit, document: str) -> bool:
    wanted = document.strip().casefold()
    if not wanted:
        return False
    return wanted in hit.title.casefold() or wanted in hit.source.casefold()


def _matches_section(hit: Hit, section: str) -> bool:
    wanted = section.strip().casefold()
    if not wanted:
        return True
    return wanted in hit.section.casefold()


def _format(hits: list[Hit]) -> str:
    return "\n\n".join(
        f"{hit.title} — {hit.section} (p. {hit.page})\n{hit.content}" for hit in hits
    )
