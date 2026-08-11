from issueflow.sandbox import build_docker_command


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
