from dataclasses import FrozenInstanceError

import pytest

from casefile.shared.result import Err, Ok, Result


def describe(result: Result[int, str]) -> str:
    match result:
        case Ok(value):
            return str(value)
        case Err(error):
            return error


def test_result_distinguishes_success_and_business_failure() -> None:
    assert describe(Ok(42)) == "42"
    assert describe(Err("invalid_transition")) == "invalid_transition"
    assert Ok(None).value is None


@pytest.mark.parametrize("result", [Ok(1), Err("refused")])
def test_result_is_immutable(result: object) -> None:
    with pytest.raises(FrozenInstanceError):
        setattr(result, "value" if isinstance(result, Ok) else "error", "changed")
