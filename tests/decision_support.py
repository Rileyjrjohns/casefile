"""Datasets that decision tests control, whatever kind of replay file `evals/` holds."""

from pathlib import Path

COMMITTED_REPLAY = Path("evals/decision-replay")


def dataset(tmp_path: Path, *providers: str, fixtures: Path = COMMITTED_REPLAY) -> Path:
    """A temporary dataset: the committed suites, plus a link to each named provider's replay file.

    `fixtures` is the directory the replay files come from. It defaults to the committed one, for
    a test about the committed dataset; a test that needs known content passes the synthetic
    fixtures it generated, so it does not depend on what is committed.
    """
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "golden").symlink_to(Path("evals/golden").resolve(), target_is_directory=True)
    (root / "replay").symlink_to(Path("evals/replay").resolve(), target_is_directory=True)
    (root / "current").symlink_to(Path("evals/current").resolve(), target_is_directory=True)
    (root / "decision-thresholds.toml").symlink_to(Path("evals/decision-thresholds.toml").resolve())
    if providers:
        (root / "decision-replay").mkdir()
        for name in providers:
            (root / "decision-replay" / f"{name}.json").symlink_to(
                (fixtures / f"{name}.json").resolve()
            )
    return root
