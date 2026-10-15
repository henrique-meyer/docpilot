"""LangChain RAG baseline. Production answers stay on the ask API."""

from __future__ import annotations

from pathlib import Path

from docpilot.ask.service import NOT_FOUND, AskResult, PassageSource, _citation, _is_not_found
from docpilot.config import Settings
from docpilot.eval.runner import EvalReport
from docpilot.retrieve.prompt import PROMPT_VERSION, render_context

_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "retrieve" / "prompts" / f"{PROMPT_VERSION}.txt"
)


class LangChainAnswerer:
    """One LCEL chain: the versioned prompt, then the chat model."""

    def __init__(self, retriever: PassageSource | None, llm: object) -> None:
        from langchain_core.prompts import ChatPromptTemplate

        self.retriever = retriever
        prompt = ChatPromptTemplate.from_template(_TEMPLATE.read_text(encoding="utf-8"))
        self.chain = prompt | llm  # type: ignore[operator]

    def ask(self, question: str) -> AskResult:
        hits = self.retriever.retrieve(question) if self.retriever is not None else []
        trace_id = _trace()
        if not hits:
            return AskResult(NOT_FOUND, [], trace_id)
        message = self.chain.invoke({"question": question, "context": render_context(hits)})
        text = _text(message)
        tokens = _tokens(message)
        if _is_not_found(text):
            return AskResult(NOT_FOUND, [], trace_id, tokens)
        return AskResult(text.strip(), [_citation(hit) for hit in hits], trace_id, tokens)


def compare_markdown(docpilot: EvalReport, baseline: EvalReport) -> str:
    """Accuracy, citation rate, latency, and tokens for the two answerers."""
    rows = (
        ("correctness", f"{docpilot.correctness:.2f}", f"{baseline.correctness:.2f}"),
        (
            "citation accuracy",
            f"{docpilot.citation_accuracy:.2f}",
            f"{baseline.citation_accuracy:.2f}",
        ),
        (
            "mean latency ms",
            f"{docpilot.mean_latency_ms:.0f}",
            f"{baseline.mean_latency_ms:.0f}",
        ),
        ("tokens", str(docpilot.total_tokens), str(baseline.total_tokens)),
    )
    lines = [
        f"# {docpilot.set_name} baseline",
        "",
        "| metric | DocPilot | LangChain |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {name} | {left} | {right} |" for name, left, right in rows)
    lines.append("")
    return "\n".join(lines)


def build_llm(settings: Settings) -> object:
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    from langchain_openai import AzureChatOpenAI

    token = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    return AzureChatOpenAI(
        azure_endpoint=settings.openai_endpoint,
        azure_deployment=settings.chat_deployment,
        api_version="2024-10-21",
        azure_ad_token_provider=token,
    )


def _trace() -> str:
    import uuid

    return uuid.uuid4().hex


def _text(message: object) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    return ""


def _tokens(message: object) -> int:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if isinstance(total, int):
            return total
    return 0
