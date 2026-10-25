from decimal import Decimal

import pytest

from casefile.features.investigation.adapters.outbound.documents import decode_run, encode_run
from casefile.features.investigation.domain.budget import BudgetUsage
from tests.contracts.support import awaiting, pending, value


def test_failed_budget_snapshot_preserves_usage_and_reason() -> None:
    active = value(pending().start_gathering())
    failed = value(active.consume(value(BudgetUsage.create(6, 101, Decimal("2")))))
    assert decode_run(encode_run(failed)) == failed


@pytest.mark.parametrize("field", ["state", "failure_reason", "schema_version"])
def test_corrupt_state_or_unknown_document_version_is_rejected(field: str) -> None:
    payload = encode_run(awaiting())
    payload[field] = {"state": "PENDING", "failure_reason": "Contradiction", "schema_version": 2}[
        field
    ]
    with pytest.raises(ValueError):
        decode_run(payload)


def test_corrupt_budget_is_not_reconstituted_unchecked() -> None:
    payload = encode_run(pending())
    payload["limits"] = {"tool_calls": -1, "tokens": 1, "estimated_cost": "0"}
    with pytest.raises(ValueError, match="Corrupt stored run"):
        decode_run(payload)
