"""Qualify strict benchmark cases with several clean replays and JSON evidence."""

import argparse
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from issueflow.benchmark import load_catalog
from issueflow.benchmark_validation import QualificationResult, qualify_case
from issueflow.environment import SandboxFactory, load_environments
from issueflow.hidden_validation import HiddenVerifier
from issueflow.run_service import GitWorkspacePreparer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--environments", type=Path, required=True)
    parser.add_argument("--replays", type=int, default=3)
    parser.add_argument(
        "--qualification-root",
        type=Path,
        default=PROJECT_ROOT / "benchmarks" / "qualification",
    )
    arguments = parser.parse_args()

    try:
        catalog = load_catalog(arguments.catalog)
        environments = load_environments(arguments.environments)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2

    sandbox_factory = SandboxFactory(environments)
    hidden_verifier = HiddenVerifier(PROJECT_ROOT / "benchmarks" / "hidden")
    patch_root = PROJECT_ROOT / "benchmarks" / "cases"

    arguments.qualification_root.mkdir(parents=True, exist_ok=True)
    failures = 0
    with TemporaryDirectory(prefix="issueflow-qualify-") as raw_workspace_root:
        workspace_preparer = GitWorkspacePreparer(
            Path(raw_workspace_root),
            PROJECT_ROOT / "benchmarks",
        )
        for case in catalog.values():
            try:
                sandbox = sandbox_factory.for_case(case)
            except KeyError as error:
                print(f"{case.id} REJECTED {error}", file=sys.stderr)
                failures += 1
                continue
            result = qualify_case(
                case,
                workspace_preparer=workspace_preparer,
                sandbox=sandbox,
                hidden_verifier=hidden_verifier,
                patch_root=patch_root,
                replays=arguments.replays,
            )
            _write_result(arguments.qualification_root, result)
            if result.accepted_split is None:
                failures += 1
                print(f"{case.id} REJECTED {result.reasons}")
            else:
                print(f"{case.id} ACCEPTED {result.reasons}")

    return int(failures > 0)


def _write_result(root: Path, result: QualificationResult) -> None:
    path = root / f"{result.case_id}.json"
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
