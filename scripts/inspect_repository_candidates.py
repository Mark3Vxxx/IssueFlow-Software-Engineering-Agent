"""Read-only inspection of repository candidates for the strict-case funnel."""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from issueflow.candidates import Candidate, load_candidates

LICENSE_FILENAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidates",
        type=Path,
        default=PROJECT_ROOT / "benchmarks" / "candidates.yaml",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=PROJECT_ROOT / ".issueflow" / "candidate-cache",
    )
    parser.add_argument("--repos", nargs="*", default=None)
    arguments = parser.parse_args()

    candidates = load_candidates(arguments.candidates)
    if arguments.repos:
        candidates = [c for c in candidates if c.repository in arguments.repos]

    arguments.cache.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        repo_dir = arguments.cache / candidate.repository.replace("/", "__")
        report = inspect(candidate, repo_dir)
        print(
            f"{candidate.repository} "
            f"license={report['license_file']} "
            f"py_lines={report['python_nonblank_lines']} "
            f"py_commits={report['commits_touching_py']} "
            f"tests={report['test_entrypoints']}"
        )
    return 0


def inspect(candidate: Candidate, repo_dir: Path) -> dict[str, object]:
    """Clone one candidate and return the selection-relevant metadata."""
    clone(candidate.url, repo_dir)
    license_file, license_sha = detect_license(repo_dir)
    return {
        "license_file": license_file,
        "license_sha256": license_sha,
        "python_nonblank_lines": python_nonblank_lines(repo_dir),
        "commits_touching_py": commits_touching_py(repo_dir),
        "test_entrypoints": test_entrypoints(repo_dir),
    }


def clone(url: str, repo_dir: Path) -> None:
    if repo_dir.exists():
        return
    run_checked(["git", "clone", "--quiet", "--depth", "1", url, str(repo_dir)], repo_dir.parent)


def detect_license(repo_dir: Path) -> tuple[str, str]:
    for filename in LICENSE_FILENAMES:
        path = repo_dir / filename
        if path.is_file():
            return filename, hashlib.sha256(path.read_bytes()).hexdigest()
    return "none", ""


def python_nonblank_lines(repo_dir: Path) -> int:
    total = 0
    for path in repo_dir.rglob("*.py"):
        if ".git" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += sum(1 for line in text.splitlines() if line.strip())
    return total


def commits_touching_py(repo_dir: Path) -> int:
    completed = subprocess.run(
        ["git", "log", "--oneline", "--", "*.py"],
        cwd=repo_dir,
        capture_output=True,
        check=False,
        text=True,
    )
    return len([line for line in completed.stdout.splitlines() if line.strip()])


def test_entrypoints(repo_dir: Path) -> list[str]:
    entrypoints: list[str] = []
    if (repo_dir / "pytest.ini").exists() or (repo_dir / "pyproject.toml").exists():
        entrypoints.append("pytest")
    if any(repo_dir.rglob("test_*.py")) or any(repo_dir.rglob("*_test.py")):
        entrypoints.append("test_files")
    return entrypoints


def run_checked(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, check=False, text=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "command failed")
    return completed


if __name__ == "__main__":
    raise SystemExit(main())
