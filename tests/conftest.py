import asyncio
from pathlib import Path

import pytest

from casefile.bootstrap.decision_benchmark import Probe, collect_probes, write_synthetic_fixtures
from tests.decision_support import dataset


@pytest.fixture(scope="session")
def recorded_probes() -> tuple[Probe, ...]:
    """Questions recorded from the agent on both evaluation suites, collected once per session."""
    return asyncio.run(collect_probes(Path("evals")))


@pytest.fixture(scope="session")
def synthetic_replay(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Synthetic Jev and LLM protocol fixtures, written once per session by the generator.

    `evals/decision-replay` holds whatever the project committed, which can be a genuine provider
    recording. A test that needs synthetic answers reads these files, which it can link or copy
    into a dataset of its own.
    """
    root = dataset(tmp_path_factory.mktemp("synthetic"))
    asyncio.run(write_synthetic_fixtures(root))
    return root / "decision-replay"
