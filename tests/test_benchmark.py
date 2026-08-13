import sys
from pathlib import Path
from subprocess import run

import pytest
import yaml

from issueflow.benchmark import load_catalog


def make_case(case_id: str, kind: str, budget_profile: str) -> dict[str, object]:
    case = {
        "id": case_id,
        "dataset_split": "compatibility",
        "repository_id": "micrograd",
        "environment_id": "micrograd",
        "kind": kind,
        "budget_profile": budget_profile,
        "difficulty": budget_profile,
        "issue_category": "numerical" if kind == "constructed" else "model_training",
        "repository_url": "https://github.com/karpathy/micrograd",
        "revision": "a" * 40,
        "license": "MIT",
        "issue": "Verify a gradient calculation",
        "source_url": "https://github.com/karpathy/micrograd",
        "reproduce_command": "python -m pytest",
        "verify_command": "python -m pytest",
        "reference_patch": f"patches/{case_id}.patch",
        "construction_notes": "A controlled regression case.",
    }
    if kind == "constructed":
        case["fault_patch"] = f"patches/{case_id}-fault.patch"
    return case


def test_catalog_accepts_the_compatibility_mix_in_declared_order(tmp_path):
    catalog_path = tmp_path / "compatibility.yaml"
    catalog_path.write_text(
        yaml.safe_dump(
            {
                "dataset_split": "compatibility",
                "cases": [
                    make_case("historical-01", "historical", "medium"),
                    make_case("constructed-01", "constructed", "small"),
                    make_case("constructed-02", "constructed", "small"),
                    make_case("constructed-03", "constructed", "small"),
                    make_case("constructed-04", "constructed", "small"),
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = load_catalog(catalog_path)

    assert list(catalog) == [
        "historical-01",
        "constructed-01",
        "constructed-02",
        "constructed-03",
        "constructed-04",
    ]
    assert {case_id: case.budget_profile for case_id, case in catalog.items()} == {
        "historical-01": "medium",
        "constructed-01": "small",
        "constructed-02": "small",
        "constructed-03": "small",
        "constructed-04": "small",
    }


def test_catalog_rejects_an_empty_case_list(tmp_path):
    catalog_path = tmp_path / "empty.yaml"
    catalog_path.write_text(
        yaml.safe_dump({"dataset_split": "compatibility", "cases": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least one case"):
        load_catalog(catalog_path)


def test_verify_script_reports_an_invalid_catalog(tmp_path):
    catalog_path = tmp_path / "invalid.yaml"
    catalog_path.write_text("cases: []", encoding="utf-8")
    script_path = Path(__file__).parents[1] / "scripts" / "verify_benchmarks.py"

    result = run(
        [sys.executable, str(script_path), "--catalog", str(catalog_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "dataset_split" in result.stderr
