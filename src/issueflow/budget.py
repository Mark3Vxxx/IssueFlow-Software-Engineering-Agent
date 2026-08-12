"""Bounded resource profiles for registered benchmark cases."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from issueflow.models import BenchmarkCase, Budget


BUDGET_PROFILES: Final[Mapping[str, Budget]] = MappingProxyType(
    {
        "small": Budget(
            max_tool_calls=12,
            max_patch_attempts=2,
            max_seconds=300,
            max_input_tokens=30_000,
            max_output_tokens=6_000,
            max_cost_usd=0.05,
        ),
        "medium": Budget(
            max_tool_calls=18,
            max_patch_attempts=4,
            max_seconds=450,
            max_input_tokens=50_000,
            max_output_tokens=8_000,
            max_cost_usd=0.10,
        ),
        "large": Budget(
            max_tool_calls=24,
            max_patch_attempts=6,
            max_seconds=600,
            max_input_tokens=80_000,
            max_output_tokens=12_000,
            max_cost_usd=0.20,
        ),
    }
)


def budget_for_case(case: BenchmarkCase) -> Budget:
    """Return an independent hard-limit object for one registered case."""
    return BUDGET_PROFILES[case.budget_profile].model_copy(deep=True)
