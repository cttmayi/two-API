import pytest
from src.config import ModelEntry
from src.router import ModelRouter


class TestModelRouter:
    @pytest.fixture
    def entries(self):
        return [
            ModelEntry(names=["gpt-4o", "gpt-4o-mini"], openai_base_url="https://api.openai.com", api_key="sk-1"),
            ModelEntry(names=["claude-sonnet-4-6"], anthropic_base_url="https://api.anthropic.com", api_key="sk-2"),
            ModelEntry(
                names=["deepseek-chat"],
                openai_base_url="https://api.deepseek.com",
                anthropic_base_url="https://api.deepseek.com/anthropic",
                api_key="sk-3",
            ),
            ModelEntry(names=["local-llama"], openai_base_url="http://localhost:8000"),
        ]

    @pytest.fixture
    def router(self, entries):
        return ModelRouter(entries)

    def test_match_openai_model(self, router):
        entry = router.match("gpt-4o", "openai")
        assert entry is not None
        assert entry.openai_base_url == "https://api.openai.com"

    def test_match_anthropic_model(self, router):
        entry = router.match("claude-sonnet-4-6", "anthropic")
        assert entry is not None
        assert entry.anthropic_base_url == "https://api.anthropic.com"

    def test_match_dual_format_model_openai(self, router):
        entry = router.match("deepseek-chat", "openai")
        assert entry is not None
        assert entry.openai_base_url == "https://api.deepseek.com"

    def test_match_dual_format_model_anthropic(self, router):
        entry = router.match("deepseek-chat", "anthropic")
        assert entry is not None
        assert entry.anthropic_base_url == "https://api.deepseek.com/anthropic"

    def test_match_unknown_model_returns_none(self, router):
        entry = router.match("nonexistent-model", "openai")
        assert entry is None

    def test_match_wrong_endpoint_type_returns_none(self, router):
        """claude-sonnet-4-6 has no openai_base_url, so matching on openai endpoint should fail."""
        entry = router.match("claude-sonnet-4-6", "openai")
        assert entry is None

    def test_match_anthropic_model_on_anthropic_endpoint(self, router):
        """gpt-4o has no anthropic_base_url, so matching on anthropic endpoint should fail."""
        entry = router.match("gpt-4o", "anthropic")
        assert entry is None

    def test_list_openai_models(self, router):
        models = router.list_models("openai")
        model_names = sorted(models)
        assert model_names == sorted(["gpt-4o", "gpt-4o-mini", "deepseek-chat", "local-llama"])

    def test_list_anthropic_models(self, router):
        models = router.list_models("anthropic")
        model_names = sorted(models)
        assert model_names == sorted(["claude-sonnet-4-6", "deepseek-chat"])
