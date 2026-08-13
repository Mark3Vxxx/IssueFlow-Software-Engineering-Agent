"""Dataset-split metadata, strict-case validation, and multi-catalog loading."""

import pytest
import yaml
from pydantic import ValidationError

from issueflow.benchmark import load_catalog, load_catalogs
from issueflow.models import BenchmarkCase


def valid_case(**updates) -> dict[str, object]:
    values = {
        "id": "mingpt-h01",
        "dataset_split": "strict",
        "repository_id": "mingpt",
        "environment_id": "mingpt",
        "kind": "historical",
        "budget_profile": "small",
        "repository_url": "https://github.com/karpathy/minGPT",
        "revision": "b" * 40,
        "license": "MIT",
        "issue": "A historical training-loop bug.",
        "source_url": "https://github.com/karpathy/minGPT/commit/fix",
        "reproduce_command": "python -m pytest tests/test_train.py",
        "verify_command": "python -m pytest tests/test_train.py",
        "reference_patch": "patches/mingpt-h01-fix.patch",
        "construction_notes": "Historical upstream repair.",
        "hidden_test_path": "mingpt-h01/test_hidden.py",
        "hidden_verify_command": "python -m pytest test_hidden.py",
        "fixed_revision": "c" * 40,
        "difficulty": "small",
        "issue_category": "model_training",
    }
    values.update(updates)
    return values


def test_strict_case_requires_hidden_validation():
    values = valid_case(hidden_test_path=None)

    with pytest.raises(ValidationError, match="strict cases require hidden validation"):
        BenchmarkCase(**values)


def test_compatibility_case_does_not_require_hidden_validation():
    case = BenchmarkCase(
        **valid_case(
            dataset_split="compatibility",
            hidden_test_path=None,
            hidden_verify_command=None,
            fixed_revision=None,
        )
    )

    assert case.hidden_test_path is None
    assert case.hidden_verify_command is None
    assert case.fixed_revision is None


def test_strict_case_requires_a_fixed_revision():
    with pytest.raises(ValidationError, match="fixed revision"):
        BenchmarkCase(**valid_case(fixed_revision=None))


def test_agent_view_exposes_only_agent_safe_fields():
    case = BenchmarkCase(**valid_case())

    assert case.agent_view().model_dump() == {
        "id": "mingpt-h01",
        "repository_id": "mingpt",
        "issue": "A historical training-loop bug.",
        "reproduce_command": "python -m pytest tests/test_train.py",
        "verify_command": "python -m pytest tests/test_train.py",
    }


def test_load_catalog_requires_a_declared_dataset_split(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump({"cases": [valid_case()]}), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset_split"):
        load_catalog(path)


def test_load_catalogs_rejects_duplicate_ids_across_splits(tmp_path):
    strict_path = tmp_path / "strict.yaml"
    exploratory_path = tmp_path / "exploratory.yaml"
    strict_path.write_text(
        yaml.safe_dump({"dataset_split": "strict", "cases": [valid_case()]}),
        encoding="utf-8",
    )
    exploratory_path.write_text(
        yaml.safe_dump(
            {
                "dataset_split": "exploratory",
                "cases": [valid_case(dataset_split="exploratory")],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="catalog case IDs must be globally unique"):
        load_catalogs([strict_path, exploratory_path])
