import pytest

from issueflow.config import Settings


def test_settings_uses_default_model_and_redacts_api_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")

    settings = Settings.from_env()

    assert settings.model == "deepseek-v4-flash"
    assert settings.temperature == 0.0
    assert "secret-value" not in settings.safe_dict().values()
    assert settings.safe_dict()["base_url"] == "https://api.deepseek.com"


@pytest.mark.parametrize("temperature", ["0", "2"])
def test_settings_accepts_temperature_bounds(monkeypatch, temperature):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")
    monkeypatch.setenv("ISSUEFLOW_TEMPERATURE", temperature)

    assert Settings.from_env().temperature == float(temperature)


@pytest.mark.parametrize("temperature", ["-0.01", "2.01"])
def test_settings_rejects_temperature_outside_bounds(monkeypatch, temperature):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")
    monkeypatch.setenv("ISSUEFLOW_TEMPERATURE", temperature)

    with pytest.raises(ValueError, match="temperature"):
        Settings.from_env()
