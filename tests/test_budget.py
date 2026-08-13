from pathlib import Path

import pytest

from issueflow.benchmark import load_catalog
from issueflow.budget import budget_for_case

EXPECTED = {
    "small": {
        "max_tool_calls": 12,
        "max_patch_attempts": 2,
        "max_seconds": 300,
        "max_input_tokens": 30_000,
        "max_output_tokens": 6_000,
        "max_cost_usd": 0.05,
    },
    "medium": {
        "max_tool_calls": 18,
        "max_patch_attempts": 4,
        "max_seconds": 450,
        "max_input_tokens": 50_000,
        "max_output_tokens": 8_000,
        "max_cost_usd": 0.10,
    },
    "large": {
        "max_tool_calls": 24,
        "max_patch_attempts": 6,
        "max_seconds": 600,
        "max_input_tokens": 80_000,
        "max_output_tokens": 12_000,
        "max_cost_usd": 0.20,
    },
}


def test_budget_profiles_have_the_documented_limits():
    catalog = load_catalog(Path("benchmarks/catalogs/compatibility.yaml"))
    historical = catalog["historical-01"]
    constructed = catalog["constructed-01"]

    assert budget_for_case(historical).model_dump() == EXPECTED["medium"]
    assert budget_for_case(constructed).model_dump() == EXPECTED["small"]


def test_budget_resolution_returns_independent_objects():
    case = load_catalog(Path("benchmarks/catalogs/compatibility.yaml"))["historical-01"]

    first = budget_for_case(case)
    second = budget_for_case(case)
    first.max_patch_attempts = 99

    assert first is not second
    assert second.model_dump() == EXPECTED["medium"]


@pytest.mark.parametrize("profile", ["small", "medium", "large"])
def test_every_named_profile_resolves_to_its_documented_limits(profile):
    case = load_catalog(Path("benchmarks/catalogs/compatibility.yaml"))[
        "constructed-01"
    ].model_copy(update={"budget_profile": profile})

    assert budget_for_case(case).model_dump() == EXPECTED[profile]
