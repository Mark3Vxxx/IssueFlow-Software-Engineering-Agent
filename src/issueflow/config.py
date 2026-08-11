"""Runtime configuration that keeps credentials out of application records."""

import os

from pydantic import BaseModel, SecretStr


class Settings(BaseModel):
    """Configuration loaded from process environment variables."""

    api_key: SecretStr
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"

    @classmethod
    def from_env(cls) -> "Settings":
        """Load the model connection details without persisting the API key."""
        return cls(
            api_key=SecretStr(os.environ["DEEPSEEK_API_KEY"]),
            model=os.getenv("ISSUEFLOW_MODEL", "deepseek-chat"),
            base_url=os.getenv("ISSUEFLOW_BASE_URL", "https://api.deepseek.com"),
        )

    def safe_dict(self) -> dict[str, str]:
        """Return the settings that are safe to place in traces and UI views."""
        return {"model": self.model, "base_url": self.base_url}
