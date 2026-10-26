"""Exercise the actual import-linter configuration on isolated Python packages."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEATURES = ("case_intake", "investigation", "review", "evaluation", "risk_scoring", "decisioning")


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    """Keep policy checks independent from future feature implementations."""
    packages = ["casefile", "casefile/shared", "casefile/bootstrap", "casefile/features"]
    for feature in FEATURES:
        base = f"casefile/features/{feature}"
        packages.append(base)
        packages.extend(
            f"{base}/{layer}"
            for layer in (
                "domain",
                "application",
                "adapters",
                "adapters/inbound",
                "adapters/outbound",
                "tests",
            )
        )
    for package in packages:
        directory = tmp_path / package
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


def lint_imports(package_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(Path(sys.executable).with_name("lint-imports")),
            "--config",
            str(PROJECT_ROOT / "pyproject.toml"),
            "--no-cache",
        ],
        cwd=package_root,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join((str(package_root), str(PROJECT_ROOT))),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize(
    ("importer", "statement", "contract"),
    [
        (
            "features/investigation/domain",
            "import casefile.features.investigation.application",
            "Dependencies point toward the domain in each feature",
        ),
        (
            "features/investigation/application",
            "import casefile.features.investigation.adapters.outbound",
            "Dependencies point toward the domain in each feature",
        ),
        (
            "features/investigation/adapters/inbound",
            "import casefile.features.review.domain",
            "Features are independent",
        ),
        (
            "features/investigation/adapters/inbound",
            "import casefile.features.investigation.adapters.outbound",
            "Inbound and outbound adapters are wired by bootstrap",
        ),
        (
            "features/investigation/domain",
            "import casefile.bootstrap",
            "Bootstrap wires features; shared stays independent",
        ),
        (
            "shared",
            "import casefile.features.investigation.domain",
            "Bootstrap wires features; shared stays independent",
        ),
        (
            "features/investigation/domain",
            "import pytest",
            "Third-party packages stay outside domain, application and shared",
        ),
        (
            "features/investigation/application",
            "import pytest",
            "Third-party packages stay outside domain, application and shared",
        ),
        (
            "shared",
            "import pytest",
            "Third-party packages stay outside domain, application and shared",
        ),
        (
            "features/investigation/domain",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pytest",
            "Third-party packages stay outside domain, application and shared",
        ),
    ],
)
def test_forbidden_import_breaks_contract(
    package_root: Path, importer: str, statement: str, contract: str
) -> None:
    module = package_root / "casefile" / importer / "example.py"
    module.write_text(f"{statement}\n", encoding="utf-8")

    result = lint_imports(package_root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{contract} BROKEN" in result.stdout, result.stdout + result.stderr


IO_CONTRACT = "Domain, application and shared stay free of I/O standard-library modules"
IO_STATEMENTS = (
    "import socket",
    "import ssl",
    "import subprocess",
    "import multiprocessing",
    "import urllib.request",
    "from urllib import parse",
    "import http.client",
    "from http import HTTPStatus",
    "import ftplib",
    "import smtplib",
    "import sqlite3",
    "from multiprocessing import pool",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import socket",
)


@pytest.mark.parametrize(
    "importer", ["features/investigation/domain", "features/review/application", "shared"]
)
@pytest.mark.parametrize("statement", IO_STATEMENTS)
def test_io_standard_library_breaks_core_layer_contract(
    package_root: Path, importer: str, statement: str
) -> None:
    module = package_root / "casefile" / importer / "example.py"
    module.write_text(f"{statement}\n", encoding="utf-8")

    result = lint_imports(package_root)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{IO_CONTRACT} BROKEN" in result.stdout, result.stdout + result.stderr
    assert "example" in result.stdout


@pytest.mark.parametrize("importer", ["features/investigation/adapters/outbound", "bootstrap"])
@pytest.mark.parametrize(
    "statement", ["import socket", "import subprocess", "import urllib.request"]
)
def test_io_standard_library_stays_available_to_adapters_and_bootstrap(
    package_root: Path, importer: str, statement: str
) -> None:
    module = package_root / "casefile" / importer / "example.py"
    module.write_text(f"{statement}\n", encoding="utf-8")

    result = lint_imports(package_root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_pure_standard_library_modules_stay_allowed_in_core_layers(package_root: Path) -> None:
    statement = "import json\nimport re\nimport asyncio\nfrom datetime import UTC\nfrom pathlib import PurePath"
    for importer in ("shared", "features/investigation/domain", "features/review/application"):
        module = package_root / "casefile" / importer / "example.py"
        module.write_text(f"{statement}\n", encoding="utf-8")

    result = lint_imports(package_root)

    assert result.returncode == 0, result.stdout + result.stderr


def test_allowed_dependencies_keep_all_contracts(package_root: Path) -> None:
    imports = {
        "shared": "from dataclasses import dataclass",
        "features/investigation/domain": "from decimal import Decimal\nimport casefile.shared",
        "features/investigation/application": (
            "import casefile.features.investigation.domain\nimport casefile.shared"
        ),
        "features/investigation/adapters/outbound": (
            "import casefile.features.investigation.application\nimport pytest"
        ),
        "features/investigation/tests": (
            "import casefile.features.investigation.adapters.outbound\nimport pytest"
        ),
        "bootstrap": (
            "import casefile.features.investigation.adapters.outbound\n"
            "import casefile.features.review.application\nimport pytest"
        ),
    }
    for importer, statement in imports.items():
        module = package_root / "casefile" / importer / "example.py"
        module.write_text(f"{statement}\n", encoding="utf-8")

    result = lint_imports(package_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "6 kept, 0 broken" in result.stdout
