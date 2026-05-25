import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory for config files during tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config_yaml():
    """Return a minimal valid config YAML string."""
    return """
server:
  host: "127.0.0.1"
  port: 8080

models:
  - names:
      - gpt-4o
    openai_base_url: https://api.openai.com
    api_key: sk-test

  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com
    api_key: sk-ant-test

logging:
  level: INFO
  output: file
  dir: ./logs
"""
