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
        result = router.match("gpt-4o", "openai")
        assert result is not None
        entry, backend = result
        assert entry.openai_base_url == "https://api.openai.com"
        assert backend == "gpt-4o"

    def test_match_anthropic_model(self, router):
        result = router.match("claude-sonnet-4-6", "anthropic")
        assert result is not None
        entry, backend = result
        assert entry.anthropic_base_url == "https://api.anthropic.com"
        assert backend == "claude-sonnet-4-6"

    def test_match_dual_format_model_openai(self, router):
        result = router.match("deepseek-chat", "openai")
        assert result is not None
        entry, backend = result
        assert entry.openai_base_url == "https://api.deepseek.com"
        assert backend == "deepseek-chat"

    def test_match_dual_format_model_anthropic(self, router):
        result = router.match("deepseek-chat", "anthropic")
        assert result is not None
        entry, backend = result
        assert entry.anthropic_base_url == "https://api.deepseek.com/anthropic"
        assert backend == "deepseek-chat"

    def test_match_alias_model(self, router):
        """Model name alias: client uses 'fast' → backend gets 'gpt-4o-mini'."""
        entries = [
            ModelEntry(names=[{"fast": "gpt-4o-mini"}], openai_base_url="https://api.openai.com", api_key="sk-1"),
        ]
        r = ModelRouter(entries)
        result = r.match("fast", "openai")
        assert result is not None
        entry, backend = result
        assert backend == "gpt-4o-mini"

    def test_match_unknown_model_returns_none(self, router):
        result = router.match("nonexistent-model", "openai")
        assert result is None

    def test_match_wrong_endpoint_type_returns_none(self, router):
        """claude-sonnet-4-6 has no openai_base_url, so matching on openai endpoint should fail."""
        result = router.match("claude-sonnet-4-6", "openai")
        assert result is None

    def test_match_anthropic_model_on_anthropic_endpoint(self, router):
        """gpt-4o has no anthropic_base_url, so matching on anthropic endpoint should fail."""
        result = router.match("gpt-4o", "anthropic")
        assert result is None

    def test_list_openai_models(self, router):
        models = router.list_models("openai")
        model_names = sorted(models)
        assert model_names == sorted(["gpt-4o", "gpt-4o-mini", "deepseek-chat", "local-llama"])

    def test_list_anthropic_models(self, router):
        models = router.list_models("anthropic")
        model_names = sorted(models)
        assert model_names == sorted(["claude-sonnet-4-6", "deepseek-chat"])
