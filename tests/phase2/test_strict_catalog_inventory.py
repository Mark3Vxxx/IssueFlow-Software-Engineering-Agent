"""Strict-catalog inventory invariants for the qualified case set."""

import re
from pathlib import Path

from issueflow.benchmark import load_catalog

STRICT_CATALOG = Path(__file__).parents[2] / "benchmarks" / "catalogs" / "strict.yaml"
ID_PATTERN = re.compile(r"^[a-z0-9-]+-h[0-9]{2}$")


def _cases():
    return list(load_catalog(STRICT_CATALOG).values())


def test_strict_catalog_has_six_qualified_historical_cases():
    cases = _cases()
    assert len(cases) == 6
    assert all(case.dataset_split.value == "strict" for case in cases)
    assert all(case.kind == "historical" for case in cases)


def test_strict_catalog_spans_the_four_selected_repositories():
    repos = {case.repository_id for case in _cases()}
    assert repos == {"mingpt", "nanogpt", "nanochat", "makemore"}


def test_strict_case_ids_follow_the_naming_rule():
    for case in _cases():
        assert ID_PATTERN.match(case.id), case.id


def test_strict_cases_have_unique_source_urls_and_patch_pairs():
    cases = _cases()
    source_urls = [case.source_url for case in cases]
    assert len(set(source_urls)) == len(source_urls)
    pairs = {(case.revision, case.reference_patch) for case in cases}
    assert len(pairs) == len(cases)
