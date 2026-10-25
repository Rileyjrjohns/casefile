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
    assert "5 kept, 0 broken" in result.stdout
