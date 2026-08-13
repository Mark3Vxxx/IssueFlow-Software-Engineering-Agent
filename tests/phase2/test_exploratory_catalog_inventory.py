"""Exploratory-catalog inventory invariants."""

from pathlib import Path

from issueflow.benchmark import load_catalog

EXPLORATORY_CATALOG = Path(__file__).parents[2] / "benchmarks" / "catalogs" / "exploratory.yaml"
STRICT_CATALOG = Path(__file__).parents[2] / "benchmarks" / "catalogs" / "strict.yaml"


def _exploratory():
    return list(load_catalog(EXPLORATORY_CATALOG).values())


def test_exploratory_catalog_has_at_least_ten_entries():
    cases = _exploratory()
    assert len(cases) >= 10
    assert all(case.dataset_split.value == "exploratory" for case in cases)


def test_exploratory_cases_record_a_failed_gate():
    for case in _exploratory():
        assert case.construction_notes.strip()
        assert "Rejected strict gate" in case.construction_notes


def test_exploratory_cases_have_provenance_and_license():
    for case in _exploratory():
        assert case.repository_url.startswith("https://")
        assert case.license
        assert case.source_url.startswith("https://")


def test_exploratory_cases_do_not_overlap_with_strict():
    strict = list(load_catalog(STRICT_CATALOG).values())
    strict_urls = {case.source_url for case in strict}
    strict_revisions = {case.revision for case in strict}
    for case in _exploratory():
        assert case.source_url not in strict_urls
        assert case.revision not in strict_revisions
