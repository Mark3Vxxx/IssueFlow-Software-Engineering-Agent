"""Runtime configuration that keeps credentials out of application records."""

import os

from pydantic import BaseModel, Field, SecretStr


class Settings(BaseModel):
    """Configuration loaded from process environment variables."""

    api_key: SecretStr
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    temperature: float = Field(default=0.0, ge=0, le=2)

    @classmethod
    def from_env(cls) -> "Settings":
        """Load the model connection details without persisting the API key."""
        return cls(
            api_key=SecretStr(os.environ["DEEPSEEK_API_KEY"]),
            model=os.getenv("ISSUEFLOW_MODEL", "deepseek-v4-flash"),
            base_url=os.getenv("ISSUEFLOW_BASE_URL", "https://api.deepseek.com"),
            temperature=float(os.getenv("ISSUEFLOW_TEMPERATURE", "0.0")),
        )

    def safe_dict(self) -> dict[str, str | float]:
        """Return the settings that are safe to place in traces and UI views."""
        return {"model": self.model, "base_url": self.base_url, "temperature": self.temperature}
