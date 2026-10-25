"""The scaffold must be importable as an installed package."""

import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "casefile.bootstrap",
        "casefile.shared",
        "casefile.features.case_intake.domain",
        "casefile.features.investigation.domain",
        "casefile.features.review.domain",
        "casefile.features.evaluation.domain",
        "casefile.features.risk_scoring.domain",
        "casefile.features.decisioning.domain",
    ],
)
def test_installed_package_imports(module: str) -> None:
    assert importlib.import_module(module).__name__ == module
