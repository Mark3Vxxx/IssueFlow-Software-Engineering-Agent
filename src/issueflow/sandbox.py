"""Construct and run resource-limited Docker commands for repair tasks."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

IMAGE_NAME = "issueflow-micrograd:dev"
MAX_OUTPUT_CHARS = 20_000


@dataclass(frozen=True)
class SandboxResult:
    """Normalized result of one container command."""

    returncode: int
    output: str
    timed_out: bool
    duration_ms: int


def build_docker_command(
    workspace: Path,
    command: str,
    timeout_seconds: int,
    image_name: str = IMAGE_NAME,
) -> list[str]:
    """Build the fixed isolation boundary for a single workspace command."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return _docker_command(workspace, timeout_seconds, image_name) + ["sh", "-lc", command]


def build_hidden_docker_command(
    workspace: Path,
    command: str,
    timeout_seconds: int,
    image_name: str,
    hidden_test_path: Path,
) -> list[str]:
    """Build a validator-only command that mounts one read-only hidden test."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return _docker_command(workspace, timeout_seconds, image_name, hidden_test_path) + [
        "sh",
        "-lc",
        command,
    ]


def _docker_command(
    workspace: Path,
    timeout_seconds: int,
    image_name: str,
    hidden_test_path: Path | None = None,
) -> list[str]:
    """Build the shared run flags, volumes, workdir, and image for one command."""
    del timeout_seconds  # validated by the public builders
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--pids-limit",
        "256",
        "--cpus",
        "2",
        "--memory",
        "4g",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=512m",
        "--volume",
        f"{workspace.resolve()}:/workspace:rw",
    ]
    if hidden_test_path is not None:
        command += [
            "--volume",
            f"{hidden_test_path.resolve()}:/issueflow-hidden/test_hidden.py:ro",
        ]
    command += ["--workdir", "/workspace", image_name]
    return command


def run_docker_command(docker_command: list[str], timeout_seconds: int) -> SandboxResult:
    """Run one built docker command with a host-side deadline and bounded output."""
    started_at = monotonic()
    try:
        completed = subprocess.run(
            docker_command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
        output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        return SandboxResult(
            returncode=completed.returncode,
            output=output[:MAX_OUTPUT_CHARS],
            timed_out=False,
            duration_ms=int((monotonic() - started_at) * 1_000),
        )
    except subprocess.TimeoutExpired as error:
        output = error.output or error.stderr or "command timed out"
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return SandboxResult(
            returncode=124,
            output=output.strip()[:MAX_OUTPUT_CHARS],
            timed_out=True,
            duration_ms=int((monotonic() - started_at) * 1_000),
        )


class DockerSandbox:
    """Run registered commands inside the fixed Docker isolation boundary."""

    def __init__(self, image_name: str = IMAGE_NAME) -> None:
        self.image_name = image_name

    def run(self, workspace: Path, command: str, timeout_seconds: int) -> SandboxResult:
        """Execute a command with a host-side deadline and bounded captured output."""
        docker_command = build_docker_command(workspace, command, timeout_seconds, self.image_name)
        return run_docker_command(docker_command, timeout_seconds)
