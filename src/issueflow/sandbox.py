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


def build_docker_command(workspace: Path, command: str, timeout_seconds: int) -> list[str]:
    """Build the fixed isolation boundary for a single workspace command."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return [
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
        "--workdir",
        "/workspace",
        IMAGE_NAME,
        "sh",
        "-lc",
        command,
    ]


class DockerSandbox:
    """Run registered commands inside the fixed Docker isolation boundary."""

    def run(self, workspace: Path, command: str, timeout_seconds: int) -> SandboxResult:
        """Execute a command with a host-side deadline and bounded captured output."""
        docker_command = build_docker_command(workspace, command, timeout_seconds)
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
