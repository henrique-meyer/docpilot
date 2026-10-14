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
