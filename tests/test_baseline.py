from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from docpilot.ask.service import NOT_FOUND
from docpilot.eval.baseline import LangChainAnswerer, compare_markdown
from docpilot.eval.runner import EvalReport
from docpilot.retrieve.context import Hit


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


class HitRetriever:
    def retrieve(self, question: str) -> list[Hit]:
        del question
        return [_hit()]


class EmptyRetriever:
    def retrieve(self, question: str) -> list[Hit]:
        del question
        return []


class RecordingModel(Runnable[object, AIMessage]):
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    def invoke(self, value: object, config: object = None, **kwargs: object) -> AIMessage:
        del config, kwargs
        rendered = value.to_string() if hasattr(value, "to_string") else str(value)
        self.prompts.append(rendered)
        return AIMessage(
            content=self.text,
            usage_metadata={"input_tokens": 4, "output_tokens": 3, "total_tokens": 7},
        )


def _report(name: str, correct: float, citations: float, latency: float, tokens: int) -> EvalReport:
    return EvalReport(
        name,
        (),
        correct,
        citations,
        0.8,
        1.0,
        latency,
        tokens,
        True,
        (),
    )


def test_chain_answers_from_the_retrieved_passage() -> None:
    model = RecordingModel("The deduction requires exclusive use.")
    answer = LangChainAnswerer(HitRetriever(), model).ask("home office")
    assert answer.answer == "The deduction requires exclusive use."
    assert answer.citations[0]["doc"] == "2025 Publication 587"
    assert answer.citations[0]["page"] == 2
    assert answer.tokens == 7
    assert "home office" in model.prompts[0]
    assert "Exclusive use is required" in model.prompts[0]


def test_chain_without_passages_does_not_call_the_model() -> None:
    model = RecordingModel("invented")
    answer = LangChainAnswerer(EmptyRetriever(), model).ask("home office")
    assert answer.answer == NOT_FOUND
    assert answer.citations == []
    assert model.prompts == []


def test_compare_report_lists_accuracy_citations_latency_and_tokens() -> None:
    text = compare_markdown(
        _report("irs", 0.91, 0.95, 120, 40),
        _report("irs", 0.80, 0.90, 180, 55),
    )
    assert "| correctness | 0.91 | 0.80 |" in text
    assert "| citation accuracy | 0.95 | 0.90 |" in text
    assert "| mean latency ms | 120 | 180 |" in text
    assert "| tokens | 40 | 55 |" in text
