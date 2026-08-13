"""Validator-only hidden-test execution that never reaches Agent workspaces."""

from pathlib import Path

from issueflow.models import BenchmarkCase
from issueflow.sandbox import (
    DockerSandbox,
    SandboxResult,
    build_hidden_docker_command,
    run_docker_command,
)

HIDDEN_MOUNT_LABEL = "validator-only hidden test"


class HiddenVerifier:
    """Run one catalog-owned hidden check with a read-only validator mount."""

    def __init__(self, hidden_root: Path) -> None:
        self.hidden_root = hidden_root.resolve()

    def verify(
        self,
        case: BenchmarkCase,
        workspace: Path,
        sandbox: DockerSandbox,
        timeout_seconds: int,
    ) -> SandboxResult:
        """Execute the hidden verification after public verification has passed."""
        if not case.hidden_verify_command:
            raise ValueError("case has no hidden verify command")
        hidden_path = self.resolve_hidden_test(case.hidden_test_path)
        docker_command = build_hidden_docker_command(
            workspace,
            case.hidden_verify_command,
            timeout_seconds,
            sandbox.image_name,
            hidden_path,
        )
        return run_docker_command(docker_command, timeout_seconds)

    def resolve_hidden_test(self, path_value: str | None) -> Path:
        """Resolve a hidden-test path inside the validator-only root."""
        if not path_value:
            raise ValueError("strict cases require a hidden test path")
        resolved = (self.hidden_root / path_value).resolve()
        try:
            resolved.relative_to(self.hidden_root)
        except ValueError as error:
            raise ValueError("hidden test must stay inside hidden root") from error
        return resolved
