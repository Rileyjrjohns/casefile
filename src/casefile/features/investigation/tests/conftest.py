import time
from collections.abc import Iterator

import pytest


@pytest.fixture
def local_timezone(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run with a non-UTC process timezone so an implicit local conversion becomes visible."""
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
