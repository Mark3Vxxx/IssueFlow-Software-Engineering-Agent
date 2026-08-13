"""Repository-specific environment registry and sandbox image selection."""

from pathlib import Path

import pytest
import yaml

from issueflow.environment import SandboxFactory, load_environments
from issueflow.models import BenchmarkCase
from issueflow.sandbox import DockerSandbox


def make_environment_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "environments.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "environments": [
                    {
                        "id": "micrograd",
                        "image": "issueflow-micrograd:phase2",
                        "dockerfile": "docker/Dockerfile.micrograd",
                        "python_version": "3.12",
                        "lock_digest": "sha256:" + "0" * 64,
                    },
                    {
                        "id": "mingpt",
                        "image": "issueflow-mingpt:phase2",
                        "dockerfile": "docker/Dockerfile.mingpt",
                        "python_version": "3.12",
                        "lock_digest": "sha256:" + "1" * 64,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def make_case() -> BenchmarkCase:
    return BenchmarkCase(
        id="constructed-01",
        dataset_split="compatibility",
        repository_id="micrograd",
        environment_id="micrograd",
        kind="constructed",
        budget_profile="small",
        difficulty="small",
        issue_category="numerical",
        repository_url="https://github.com/karpathy/micrograd",
        revision="a" * 40,
        license="MIT",
        issue="Unary negation returns the wrong value",
        source_url="https://github.com/karpathy/micrograd",
        reproduce_command="python -c 'raise SystemExit(1)'",
        verify_command="python -c 'raise SystemExit(0)'",
        fault_patch="patches/constructed-01-fault.patch",
        reference_patch="patches/constructed-01-fix.patch",
        construction_notes="Controlled arithmetic regression.",
    )


def test_environment_registry_resolves_pinned_image(tmp_path):
    registry = load_environments(make_environment_yaml(tmp_path))

    assert registry["mingpt"].image == "issueflow-mingpt:phase2"
    assert registry["mingpt"].dockerfile == "docker/Dockerfile.mingpt"


def test_load_environments_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "dup.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "environments": [
                    {
                        "id": "mingpt",
                        "image": "a",
                        "dockerfile": "d",
                        "python_version": "3.12",
                        "lock_digest": "x",
                    },
                    {
                        "id": "mingpt",
                        "image": "b",
                        "dockerfile": "d",
                        "python_version": "3.12",
                        "lock_digest": "x",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="environment IDs must be unique"):
        load_environments(path)


def test_sandbox_factory_resolves_case_environment(tmp_path):
    factory = SandboxFactory(load_environments(make_environment_yaml(tmp_path)))

    sandbox = factory.for_case(make_case())

    assert isinstance(sandbox, DockerSandbox)
    assert sandbox.image_name == "issueflow-micrograd:phase2"


def test_sandbox_factory_rejects_unknown_environment(tmp_path):
    factory = SandboxFactory(load_environments(make_environment_yaml(tmp_path)))
    case = make_case().model_copy(update={"environment_id": "missing"})

    with pytest.raises(KeyError, match="unknown environment"):
        factory.for_case(case)
