"""Verify every IssueFlow benchmark can fail before and pass after its reference fix."""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from issueflow.benchmark import load_catalog
from issueflow.models import BenchmarkCase


def run_checked(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a Git command and surface its stderr on failure."""
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "command failed")
    return result


def run_case_command(command: str, workspace: Path) -> subprocess.CompletedProcess[str]:
    """Run catalog commands with the current interpreter and checked-out repository on PYTHONPATH."""
    python_command = shlex.quote(sys.executable)
    command = command.replace("python ", f"{python_command} ", 1)
    environment = os.environ | {"PYTHONPATH": str(workspace)}
    return subprocess.run(
        command,
        cwd=workspace,
        env=environment,
        shell=True,
        text=True,
        capture_output=True,
        check=False,
    )


def verify_case(case: BenchmarkCase, patch_root: Path, temporary_root: Path) -> tuple[bool, str]:
    """Return whether one sample fails before and passes after the documented patch."""
    workspace = temporary_root / case.id
    run_checked(["git", "clone", "--quiet", case.repository_url, str(workspace)], temporary_root)
    run_checked(["git", "checkout", "--quiet", case.revision], workspace)

    if case.fault_patch:
        run_checked(["git", "apply", str(patch_root / case.fault_patch)], workspace)

    reproduction = run_case_command(case.reproduce_command, workspace)
    if reproduction.returncode == 0:
        return False, "reproduction unexpectedly passed"

    run_checked(["git", "apply", str(patch_root / case.reference_patch)], workspace)
    verification = run_case_command(case.verify_command, workspace)
    if verification.returncode != 0:
        return False, verification.stderr.strip() or "verification command failed"

    return True, "reproduction=FAIL_AS_EXPECTED verification=PASS"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.catalog = arguments.catalog.resolve()
    try:
        catalog = load_catalog(arguments.catalog)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2

    failures = 0
    with TemporaryDirectory(prefix="issueflow-benchmark-") as raw_temporary_root:
        temporary_root = Path(raw_temporary_root)
        for case in catalog.values():
            try:
                passed, detail = verify_case(case, PROJECT_ROOT / "benchmarks", temporary_root)
            except RuntimeError as error:
                passed, detail = False, str(error)
            outcome = "PASS" if passed else "FAIL"
            print(f"{case.id} {outcome} {detail}")
            failures += not passed
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
