import asyncio
from pathlib import Path

import pytest

from casefile.bootstrap.decision_benchmark import Probe, collect_probes


@pytest.fixture(scope="session")
def recorded_probes() -> tuple[Probe, ...]:
    """Questions recorded from the agent on both evaluation suites, collected once per session."""
    return asyncio.run(collect_probes(Path("evals")))
