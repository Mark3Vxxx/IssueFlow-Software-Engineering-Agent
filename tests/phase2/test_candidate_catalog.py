"""Repository candidate metadata and primary-quota invariants."""

from pathlib import Path

from issueflow.candidates import load_candidates

CANDIDATES_PATH = Path(__file__).parents[2] / "benchmarks" / "candidates.yaml"


def test_candidates_have_exact_owner_name_and_https_url():
    candidates = load_candidates(CANDIDATES_PATH)

    assert [c.repository for c in candidates] == [
        "karpathy/minGPT",
        "karpathy/nanoGPT",
        "karpathy/nanochat",
        "karpathy/makemore",
    ]
    for candidate in candidates:
        assert candidate.url == f"https://github.com/{candidate.repository}"
        assert candidate.license == "MIT"
        assert candidate.source_url.startswith("https://github.com/")
        assert candidate.priority > 0
        assert candidate.target_quota >= 0


def test_primary_candidates_total_target_quota_is_twenty():
    candidates = load_candidates(CANDIDATES_PATH)

    assert [c.priority for c in candidates] == [1, 2, 3, 4]
    assert sum(c.target_quota for c in candidates) == 20


def test_priorities_are_strictly_ordered():
    candidates = load_candidates(CANDIDATES_PATH)

    priorities = [c.priority for c in candidates]
    assert priorities == sorted(priorities)
    assert len(set(priorities)) == len(priorities)
