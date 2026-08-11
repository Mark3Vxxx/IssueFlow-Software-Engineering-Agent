"""Construct and run resource-limited Docker commands for repair tasks."""

from pathlib import Path

IMAGE_NAME = "issueflow-micrograd:dev"


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
