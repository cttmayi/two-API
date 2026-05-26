import pytest
import yaml
from pathlib import Path
from src.config import Config, ModelEntry, ServerConfig, LoggingConfig, load_config


class TestModelEntry:
    def test_valid_openai_only(self):
        entry = ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-xxx")
        assert entry.names == ["gpt-4o"]
        assert entry.openai_base_url == "https://api.openai.com"
        assert entry.anthropic_base_url is None

    def test_valid_both_base_urls(self):
        entry = ModelEntry(
            names=["deepseek-chat"],
            openai_base_url="https://api.deepseek.com",
            anthropic_base_url="https://api.deepseek.com/anthropic",
        )
        assert entry.openai_base_url == "https://api.deepseek.com"
        assert entry.anthropic_base_url == "https://api.deepseek.com/anthropic"

    def test_missing_both_base_urls_raises(self):
        with pytest.raises(ValueError, match="At least one of openai_base_url or anthropic_base_url must be set"):
            ModelEntry(names=["bad-model"])

    def test_empty_names_raises(self):
        with pytest.raises(ValueError):
            ModelEntry(names=[], openai_base_url="https://api.openai.com")

    def test_api_key_optional(self):
        entry = ModelEntry(names=["local"], openai_base_url="http://localhost:8000")
        assert entry.api_key is None

    def test_alias_names(self):
        entry = ModelEntry(names=[{"fast": "gpt-4o-mini"}, "gpt-4o"], openai_base_url="https://api.openai.com")
        assert entry.get_name_map() == {"fast": "gpt-4o-mini", "gpt-4o": "gpt-4o"}
        assert sorted(entry.client_names) == sorted(["fast", "gpt-4o"])


class TestServerConfig:
    def test_defaults(self):
        cfg = ServerConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8080


class TestLoggingConfig:
    def test_defaults(self):
        cfg = LoggingConfig()
        assert cfg.level == "INFO"
        assert cfg.output == "file"
        assert cfg.dir == "~/.two-api/logs"


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path):
        data = {
            "server": {"host": "127.0.0.1", "port": 9000},
            "models": [
                {"names": ["gpt-4o"], "openai_base_url": "https://api.openai.com", "api_key": "sk-xxx"},
                {"names": ["claude-sonnet-4-6"], "anthropic_base_url": "https://api.anthropic.com"},
            ],
            "logging": {"level": "DEBUG", "output": "file", "dir": "/var/log"},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))

        cfg = load_config(str(path))
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 9000
        assert len(cfg.models) == 2
        assert cfg.models[0].names == ["gpt-4o"]
        assert cfg.models[1].names == ["claude-sonnet-4-6"]
        assert cfg.logging.level == "DEBUG"

    def test_alias_default_value(self, tmp_path):
        data = {
            "models": [{"names": ["gpt-4o"], "openai_base_url": "https://api.openai.com"}],
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))
        cfg = load_config(str(path))
        assert cfg.alias == {}

    def test_alias_with_entries(self, tmp_path):
        data = {
            "models": [{"names": ["gpt-4o"], "openai_base_url": "https://api.openai.com"}],
            "alias": {"default": "gpt-4o-mini", "pro": "gpt-4o"},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))
        cfg = load_config(str(path))
        assert cfg.alias == {"default": "gpt-4o-mini", "pro": "gpt-4o"}

    def test_load_config_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")

    def test_load_config_invalid_yaml(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("not: valid: yaml: [")
        with pytest.raises(yaml.YAMLError):
            load_config(str(path))