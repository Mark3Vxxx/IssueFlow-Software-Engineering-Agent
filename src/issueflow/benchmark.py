"""Benchmark catalog loading and validation."""

from pathlib import Path

import yaml

from issueflow.models import BenchmarkCase


def load_catalog(path: Path) -> dict[str, BenchmarkCase]:
    """Load the approved micrograd sample mix in its declared order."""
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = [BenchmarkCase.model_validate(item) for item in parsed.get("cases", [])]
    historical_count = sum(case.kind == "historical" for case in cases)
    constructed_count = sum(case.kind == "constructed" for case in cases)
    if historical_count != 1 or constructed_count != 4:
        raise ValueError("catalog must contain 1 historical and 4 constructed cases")
    catalog = {case.id: case for case in cases}
    if len(catalog) != len(cases):
        raise ValueError("catalog case IDs must be unique")
    return catalog
