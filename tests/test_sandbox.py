from subprocess import CompletedProcess, TimeoutExpired

from issueflow.sandbox import DockerSandbox, build_docker_command


def test_docker_command_is_network_isolated_and_resource_limited(tmp_path):
    command = build_docker_command(
        workspace=tmp_path,
        command="python -m pytest",
        timeout_seconds=60,
    )

    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cpus") + 1] == "2"
    assert command[command.index("--memory") + 1] == "4g"
    assert command[-4:] == ["issueflow-micrograd:dev", "sh", "-lc", "python -m pytest"]


def test_docker_image_has_the_test_runtime():
    from pathlib import Path
    from subprocess import run

    project_root = Path(__file__).parents[1]
    build = run(
        [
            "docker",
            "build",
            "--tag",
            "issueflow-micrograd:dev",
            "--file",
            "docker/Dockerfile.micrograd",
            ".",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr

    runtime = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "issueflow-micrograd:dev",
            "python",
            "-c",
            "import numpy; import pytest; import torch",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert runtime.returncode == 0, runtime.stderr


def test_docker_sandbox_returns_process_result(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["timeout"] = kwargs["timeout"]
        return CompletedProcess(command, 0, stdout="2 passed\n", stderr="")

    monkeypatch.setattr("issueflow.sandbox.subprocess.run", fake_run)

    result = DockerSandbox().run(tmp_path, "python -m pytest", timeout_seconds=30)

    assert "--network" in captured["command"]
    assert captured["timeout"] == 30
    assert result.returncode == 0
    assert result.output == "2 passed"
    assert result.timed_out is False


def test_docker_sandbox_normalizes_timeout(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        raise TimeoutExpired(command, kwargs["timeout"], output="partial output")

    monkeypatch.setattr("issueflow.sandbox.subprocess.run", fake_run)

    result = DockerSandbox().run(tmp_path, "python -m pytest", timeout_seconds=1)

    assert result.returncode == 124
    assert result.output == "partial output"
    assert result.timed_out is True
