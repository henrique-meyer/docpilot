"""Grounded answers. The chat client is injected so tests stay offline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from docpilot.config import Settings
from docpilot.retrieve.context import Hit
from docpilot.retrieve.prompt import build_prompt

NOT_FOUND = "not found in the documents"
DEMO_LIMIT = "demo limit reached"
SNIPPET_CHARS = 240


class PassageSource(Protocol):
    def retrieve(self, question: str) -> list[Hit]: ...


class Chat(Protocol):
    def complete(self, prompt: str) -> tuple[str, int]: ...


@dataclass(frozen=True)
class AskResult:
    answer: str
    citations: list[dict[str, object]]
    trace_id: str
    tokens: int = 0


class Asker:
    def __init__(self, retriever: PassageSource | None, chat: Chat | None) -> None:
        self.retriever = retriever
        self.chat = chat

    def ask(self, question: str) -> AskResult:
        trace_id = uuid.uuid4().hex
        hits = self.retriever.retrieve(question) if self.retriever is not None else []
        if not hits or self.chat is None:
            return AskResult(NOT_FOUND, [], trace_id)
        text, tokens = self.chat.complete(build_prompt(question, hits))
        if _is_not_found(text):
            return AskResult(NOT_FOUND, [], trace_id, tokens)
        return AskResult(text.strip(), [_citation(hit) for hit in hits], trace_id, tokens)


def _is_not_found(text: str) -> bool:
    return text.strip().rstrip(".").casefold() == NOT_FOUND


def _citation(hit: Hit) -> dict[str, object]:
    snippet = " ".join(hit.content.split())
    if len(snippet) > SNIPPET_CHARS:
        snippet = snippet[: SNIPPET_CHARS - 1].rstrip() + "…"
    citation: dict[str, object] = {
        "doc": hit.title,
        "section": hit.section,
        "page": hit.page,
        "snippet": snippet,
    }
    if hit.year:
        citation["year"] = hit.year
    return citation


class AzureChat:
    def __init__(self, endpoint: str, deployment: str) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._deployment = deployment
        self._client: object | None = None

    def complete(self, prompt: str) -> tuple[str, int]:
        client = self._openai()
        response = client.chat.completions.create(  # type: ignore[attr-defined]
            model=self._deployment,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        tokens = int(usage.total_tokens) if usage is not None else 0
        return text, tokens

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


def build_chat(settings: Settings) -> AzureChat | None:
    if not settings.openai_endpoint:
        return None
    return AzureChat(settings.openai_endpoint, settings.chat_deployment)
