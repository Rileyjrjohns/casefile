"""Seed documents preserve provenance and reject broken demo datasets."""

from importlib.resources import files

import pytest
from pydantic import ValidationError

from casefile.features.investigation.adapters.outbound.tools.seed import ToolSeed, load_seed

pytestmark = pytest.mark.mcp


def test_bundled_seed_is_deterministic_and_explicitly_fictional() -> None:
    seed = load_seed()
    assert seed == load_seed()
    assert seed.fictional
    assert len(seed.kyc) == 3
    assert len(seed.transactions) == 4
    assert {item.kind for item in seed.alerts} == {
        "unusual_transaction",
        "sanctions_match",
        "kyc_mismatch",
    }
    assert (
        files("casefile.features.investigation.adapters.outbound.tools")
        .joinpath("seed.json")
        .is_file()
    )


@pytest.mark.parametrize("defect", ["customer", "record", "orphan", "version", "real", "decimal"])
def test_invalid_seed_is_rejected(defect: str) -> None:
    data = load_seed().model_dump(mode="json")
    if defect == "customer":
        profile = data["kyc"][0].copy()
        profile["record_id"] = "kyc-099"
        data["kyc"].append(profile)
    elif defect == "record":
        data["transactions"].append(data["transactions"][0])
    elif defect == "orphan":
        data["notes"][0]["customer_id"] = "demo-customer-999"
    elif defect == "version":
        data["schema_version"] = 2
    elif defect == "real":
        data["fictional"] = False
    else:
        data["transactions"][0]["amount"] = "1.001"
    with pytest.raises(ValidationError):
        ToolSeed.model_validate(data)
