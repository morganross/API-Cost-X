import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "debug_value, expected",
    [
        ("false", False),
        ("release", False),
        ("production", False),
        ("true", True),
        ("on", True),
    ],
)
def test_debug_env_parser_accepts_common_boolean_and_deployment_values(
    monkeypatch,
    debug_value,
    expected,
):
    monkeypatch.setenv("DEBUG", debug_value)

    settings = Settings()

    assert settings.debug is expected


def test_local_gui_cors_origins_are_allowed_when_debug_is_false(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")

    settings = Settings()

    assert "http://127.0.0.1:5173" in settings.resolved_cors_origins
    assert "http://localhost:5173" in settings.resolved_cors_origins


def test_extra_cors_origins_are_appended_and_deduplicated(monkeypatch):
    monkeypatch.setenv("API_COST_X_CORS_ORIGINS", "http://example.local,http://127.0.0.1:5173")

    settings = Settings()

    assert settings.resolved_cors_origins.count("http://127.0.0.1:5173") == 1
    assert "http://example.local" in settings.resolved_cors_origins
