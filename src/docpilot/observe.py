"""JSON logs and OpenTelemetry spans.

Info logs carry a trace id. They do not carry secrets or prompt text.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, Tracer

from docpilot.config import Settings

_MESSAGE_CHARS = 300
_trace_id: ContextVar[str] = ContextVar("docpilot_trace_id", default="")
_provider: TracerProvider | None = None
_tracer: Tracer | None = None
_otlp_ready = False
_insights_ready = False
_logging_ready = False

_SECRET = re.compile(
    r"(?i)\b(accountkey|sharedaccesskey|sharedaccesssignature|instrumentationkey|"
    r"password|secret|api[-_]?key|authorization)\b\s*[=:]\s*\S+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+")


def bind_trace(trace_id: str) -> None:
    _trace_id.set(trace_id)


def current_trace_id() -> str:
    return _trace_id.get()


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Exception text is reduced to the type name."""

    def format(self, record: logging.LogRecord) -> str:
        message = _limit(_redact(record.getMessage()))
        payload: dict[str, object] = {
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": message,
            "trace_id": current_trace_id(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["error"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    """Attach a JSON handler once. Existing handlers, including test capture, stay."""
    global _logging_ready
    if _logging_ready:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _logging_ready = True


def configure_tracing(settings: Settings) -> None:
    """Export to Jaeger when an OTLP endpoint is set, and to Application Insights in Azure."""
    global _otlp_ready, _insights_ready
    _ensure_provider(settings.otel_sample_ratio)
    if settings.otlp_endpoint and not _otlp_ready:
        _add_otlp(settings.otlp_endpoint)
        _otlp_ready = True
    if settings.appinsights_connection_string and not _insights_ready:
        _add_insights(settings.appinsights_connection_string)
        _insights_ready = True


def provider() -> TracerProvider:
    return _ensure_provider(1.0)


def otlp_traces_url(endpoint: str) -> str:
    base = endpoint.strip().rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


@contextmanager
def span(name: str, **attributes: str | float | bool) -> Iterator[Span]:
    """Record latency on the way out. Callers add token counts themselves."""
    tracer = _ensure_tracer()
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            current.set_attribute(key, value)
        trace_id = current_trace_id()
        if trace_id:
            current.set_attribute("trace_id", trace_id)
        started = time.perf_counter()
        try:
            yield current
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            current.set_attribute("latency_ms", round(elapsed, 3))


def _ensure_provider(sample_ratio: float) -> TracerProvider:
    global _provider, _tracer
    if _provider is None:
        ratio = min(1.0, max(0.0, sample_ratio))
        _provider = TracerProvider(sampler=ParentBased(TraceIdRatioBased(ratio)))
        _tracer = _provider.get_tracer("docpilot")
    return _provider


def _ensure_tracer() -> Tracer:
    _ensure_provider(1.0)
    assert _tracer is not None
    return _tracer


def _add_otlp(endpoint: str) -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    assert _provider is not None
    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_traces_url(endpoint)))
    )


def _add_insights(connection_string: str) -> None:
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    assert _provider is not None
    exporter = AzureMonitorTraceExporter(connection_string=connection_string)
    _provider.add_span_processor(BatchSpanProcessor(exporter))


def _redact(text: str) -> str:
    text = _BEARER.sub("Bearer <redacted>", text)
    return _SECRET.sub(lambda match: f"{match.group(1)}=<redacted>", text)


def _limit(text: str) -> str:
    if len(text) <= _MESSAGE_CHARS:
        return text
    return text[: _MESSAGE_CHARS - 1].rstrip() + "…"
