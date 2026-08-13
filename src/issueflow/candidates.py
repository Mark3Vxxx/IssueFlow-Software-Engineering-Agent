"""Repository candidate metadata for the strict-case selection funnel."""

from pathlib import Path

import yaml
from pydantic import BaseModel, NonNegativeInt, PositiveInt


class Candidate(BaseModel):
    """One candidate repository in its fixed selection order."""

    repository: str
    url: str
    license: str
    priority: PositiveInt
    target_quota: NonNegativeInt
    source_url: str


def load_candidates(path: Path) -> list[Candidate]:
    """Load the fixed candidate order, preserving declaration order."""
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = parsed.get("candidates")
    if not isinstance(raw, list) or not raw:
        raise ValueError("candidates must declare at least one candidate")
    return [Candidate.model_validate(item) for item in raw]
