import json
import logging

from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from docpilot.agent.tools import ToolKit
from docpilot.ask.service import Asker
from docpilot.observe import JsonFormatter, bind_trace, otlp_traces_url, provider
from docpilot.retrieve.context import Hit
from docpilot.retrieve.retriever import Retriever

_EXPORTER = InMemorySpanExporter()
provider().add_span_processor(SimpleSpanProcessor(_EXPORTER))


def _hit() -> Hit:
    return Hit(
        id="chunk",
        document_id="doc",
        title="2025 Publication 587",
        source="p587.pdf",
        section="Exclusive use",
        page=2,
        year="2025",
        content="Exclusive use is required for the deduction.",
        score=1.0,
    )


class HitEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        assert texts == ["home office"]
        return [[0.1, 0.2]]


class HitSearcher:
    def search(self, question: str, vector: list[float], top_k: int) -> list[Hit]:
        assert question == "home office"
        del vector, top_k
        return [_hit()]


class HitRetriever:
    def retrieve(self, question: str) -> list[Hit]:
        del question
        return [_hit()]


class ScriptedChat:
    def complete(self, prompt: str) -> tuple[str, int]:
        assert "Exclusive use is required" in prompt
        return "The deduction requires exclusive use.", 11


def setup_function() -> None:
    _EXPORTER.clear()


def _attributes(name: str) -> dict[str, object]:
    spans = [span for span in _EXPORTER.get_finished_spans() if span.name == name]
    assert spans, name
    return dict(spans[-1].attributes or {})


def test_json_log_keeps_the_trace_id_and_drops_secrets() -> None:
    bind_trace("trace-abc")
    prompt = "AccountKey=supersecret Bearer abc.def.ghi " + ("prompt " * 80)
    record = logging.LogRecord("docpilot.ask", logging.INFO, __file__, 1, prompt, (), None)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["trace_id"] == "trace-abc"
    assert payload["level"] == "info"
    assert "supersecret" not in payload["message"]
    assert "abc.def.ghi" not in payload["message"]
    assert len(payload["message"]) <= 300


def test_ask_spans_record_tokens_and_omit_the_question() -> None:
    result = Asker(HitRetriever(), ScriptedChat()).ask("swordfish in the home office")
    api = _attributes("api.ask")
    model = _attributes("llm")
    assert result.tokens == 11
    assert api["tokens"] == 11
    assert api["hits"] == 1
    assert api["trace_id"] == result.trace_id
    assert isinstance(api["latency_ms"], float)
    assert model["tokens"] == 11
    blob = json.dumps({"api": api, "llm": model})
    assert "swordfish" not in blob


def test_retrieval_span_counts_hits_and_omits_the_question() -> None:
    hits = Retriever(HitEmbedder(), HitSearcher(), top_k=4, token_budget=1000).retrieve("home office")
    recorded = _attributes("retrieval")
    assert len(hits) == 1
    assert recorded["hits"] == 1
    assert "home office" not in json.dumps(recorded)


def test_tool_span_names_the_tool() -> None:
    ToolKit(HitRetriever(), max_calls=2).search_documents("home office")
    recorded = _attributes("agent.tool")
    assert recorded["tool"] == "search_documents"
    assert "home office" not in json.dumps(recorded)


def test_otlp_url_adds_the_traces_path() -> None:
    assert otlp_traces_url("http://jaeger:4318") == "http://jaeger:4318/v1/traces"
    assert otlp_traces_url("http://jaeger:4318/v1/traces/") == "http://jaeger:4318/v1/traces"


def test_ask_log_omits_the_question() -> None:
    lines: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = Capture()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("docpilot.ask.service")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        Asker(HitRetriever(), ScriptedChat()).ask("swordfish in the home office")
    finally:
        logger.removeHandler(handler)
    assert lines
    body = json.loads(lines[-1])
    assert body["message"] == "ask completed"
    assert body["trace_id"]
    assert "swordfish" not in lines[-1]
