from docpilot.ingest.azure import AzureChunkIndex
from docpilot.retrieve.azure import AzureHybridSearch


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class _Client:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def search(self, **kwargs: object) -> list[object]:
        del kwargs
        raise _StatusError(self.status_code)


class _Counter:
    def __init__(self, usage: int, quota: int | None) -> None:
        self.usage = usage
        self.quota = quota


class _Stats:
    def __init__(self, usage: int, quota: int | None) -> None:
        self.counters = type("Counters", (), {"storage_size_counter": _Counter(usage, quota)})()


class _StatsClient:
    def __init__(self, usage: int, quota: int | None) -> None:
        self._stats = _Stats(usage, quota)

    def get_service_statistics(self) -> _Stats:
        return self._stats


def test_storage_usage_reads_the_service_counter() -> None:
    index = AzureChunkIndex("https://example.search.windows.net", "docpilot")
    index._indexes = lambda: _StatsClient(56_079_230, 52_428_800)  # type: ignore[method-assign]
    assert index.storage_usage() == (56_079_230, 52_428_800)


def test_storage_usage_without_a_quota_is_unavailable() -> None:
    index = AzureChunkIndex("https://example.search.windows.net", "docpilot")
    index._indexes = lambda: _StatsClient(10, None)  # type: ignore[method-assign]
    assert index.storage_usage() is None


def test_missing_index_returns_no_hits() -> None:
    search = AzureHybridSearch("https://example.search.windows.net", "docpilot", _Client(404))
    assert search.search("home office", [0.1, 0.2], 3) == []


def test_search_outage_is_raised() -> None:
    search = AzureHybridSearch("https://example.search.windows.net", "docpilot", _Client(503))
    try:
        search.search("home office", [0.1, 0.2], 3)
    except _StatusError as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("search outage was swallowed")
