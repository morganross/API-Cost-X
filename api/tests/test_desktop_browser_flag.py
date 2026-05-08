import pytest

from app import desktop


def test_open_browser_defaults_to_true(monkeypatch):
    monkeypatch.delenv("API_COST_X_OPEN_BROWSER", raising=False)

    assert desktop._should_open_browser() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_open_browser_can_be_disabled_with_false_values(monkeypatch, value):
    monkeypatch.setenv("API_COST_X_OPEN_BROWSER", value)

    assert desktop._should_open_browser() is False
