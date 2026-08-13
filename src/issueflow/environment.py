"""Repository-specific Docker environment registry and sandbox selection."""

from pathlib import Path

import yaml
from pydantic import BaseModel

from issueflow.models import BenchmarkCase
from issueflow.sandbox import DockerSandbox


class EnvironmentSpec(BaseModel):
    """One pinned, repository-specific CPU-only sandbox image."""

    id: str
    image: str
    dockerfile: str
    python_version: str
    lock_digest: str


def load_environments(path: Path) -> dict[str, EnvironmentSpec]:
    """Load the pinned environment registry in its declared order."""
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = parsed.get("environments")
    if not isinstance(raw, list) or not raw:
        raise ValueError("environments must declare at least one environment")
    specs = [EnvironmentSpec.model_validate(item) for item in raw]
    registry = {spec.id: spec for spec in specs}
    if len(registry) != len(specs):
        raise ValueError("environment IDs must be unique")
    return registry


class SandboxFactory:
    """Resolve one case's environment ID into a case-scoped Docker sandbox."""

    def __init__(self, environments: dict[str, EnvironmentSpec]) -> None:
        self.environments = environments

    def for_case(self, case: BenchmarkCase) -> DockerSandbox:
        """Return the sandbox bound to the case's declared environment image."""
        spec = self.environments.get(case.environment_id)
        if spec is None:
            raise KeyError(f"unknown environment: {case.environment_id}")
        return DockerSandbox(image_name=spec.image)
