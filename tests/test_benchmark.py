import sys
from pathlib import Path
from subprocess import run

import pytest
import yaml

from issueflow.benchmark import load_catalog


def make_case(case_id: str, kind: str) -> dict[str, str]:
    case = {
        "id": case_id,
        "kind": kind,
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


def test_catalog_accepts_one_historical_and_four_constructed_cases(tmp_path):
    catalog_path = tmp_path / "micrograd.yaml"
    catalog_path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    make_case("historical-01", "historical"),
                    make_case("constructed-01", "constructed"),
                    make_case("constructed-02", "constructed"),
                    make_case("constructed-03", "constructed"),
                    make_case("constructed-04", "constructed"),
                ]
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


def test_catalog_rejects_any_other_sample_mix(tmp_path):
    catalog_path = tmp_path / "micrograd.yaml"
    catalog_path.write_text(yaml.safe_dump({"cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="1 historical and 4 constructed"):
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
    assert "1 historical and 4 constructed" in result.stderr
