"""Benchmark catalog loading and validation."""

from collections.abc import Sequence
from pathlib import Path

import yaml

from issueflow.models import BenchmarkCase, DatasetSplit


def load_catalog(path: Path) -> dict[str, BenchmarkCase]:
    """Load one catalog file, preserving order and enforcing its declared split."""
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    declared_split = parsed.get("dataset_split")
    if declared_split is None:
        raise ValueError("catalog must declare dataset_split")
    try:
        split = DatasetSplit(declared_split)
    except ValueError as error:
        raise ValueError("catalog declares an unknown dataset_split") from error
    raw_cases = parsed.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("catalog must contain at least one case")
    cases = [BenchmarkCase.model_validate(item) for item in raw_cases]
    for case in cases:
        if case.dataset_split != split:
            raise ValueError("catalog case dataset_split must match the declared split")
    return _index_cases(cases)


def load_catalogs(paths: Sequence[Path]) -> dict[str, BenchmarkCase]:
    """Load several catalogs with globally unique case IDs."""
    merged: dict[str, BenchmarkCase] = {}
    for path in paths:
        for case_id, case in load_catalog(path).items():
            if case_id in merged:
                raise ValueError("catalog case IDs must be globally unique")
            merged[case_id] = case
    return merged


def _index_cases(cases: list[BenchmarkCase]) -> dict[str, BenchmarkCase]:
    catalog = {case.id: case for case in cases}
    if len(catalog) != len(cases):
        raise ValueError("catalog case IDs must be unique")
    return catalog
