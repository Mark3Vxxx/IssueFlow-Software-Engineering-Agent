from issueflow.config import Settings


def test_settings_uses_default_model_and_redacts_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")

    settings = Settings.from_env()

    assert settings.model == "deepseek-v4-flash"
    assert "secret-value" not in settings.safe_dict().values()
    assert settings.safe_dict()["base_url"] == "https://api.deepseek.com"
