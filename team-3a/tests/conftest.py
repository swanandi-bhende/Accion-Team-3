"""Keep the suite deterministic: never call a live LLM provider even if the
developer's .env enables one. Tests that need the LLM path monkeypatch it directly."""
import pytest


@pytest.fixture(autouse=True)
def _rule_based_extraction(monkeypatch):
    monkeypatch.setenv("USE_LLM_EXTRACTOR", "false")
