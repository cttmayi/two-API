import httpx
import pytest

from src.config import Config, LoggingConfig, ModelEntry
from src.main import app, usage_path_for_log_dir
from src.router import ModelRouter
from src.stats import get_stats


def test_usage_file_is_next_to_logs_directory(tmp_path):
    config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
        logging=LoggingConfig(dir=str(tmp_path / "logs")),
    )

    assert usage_path_for_log_dir(config.logging.dir) == str(tmp_path / "usage.json")


@pytest.mark.asyncio
async def test_homepage_renders_hourly_usage_chart():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    get_stats().record(
        "gpt-4o",
        "openai",
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=100,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert "Usage Statistics" not in resp.text
    assert "Hourly Token Usage" in resp.text
    assert "hourlyUsageData" in resp.text
    assert "hourly-chart" in resp.text
    assert "hourly-metric" not in resp.text


@pytest.mark.asyncio
async def test_homepage_renders_model_colored_hourly_chart_segments():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o", "ark-deepseek-v4-flash"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    get_stats().record("gpt-4o", "openai", prompt_tokens=10, completion_tokens=5, latency_ms=100)
    get_stats().record("ark-deepseek-v4-flash", "openai", prompt_tokens=7, completion_tokens=3, latency_ms=90)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert "hourly-legend" in resp.text
    assert "hourly-segment" in resp.text
    assert "hourly-tooltip" in resp.text
    assert "gpt-4o" in resp.text
    assert "ark-deepseek-v4-flash" in resp.text
    assert "position: fixed" in resp.text
    assert "Token Details" in resp.text
    assert "Avg Latency" in resp.text
    assert "Per Output Token" in resp.text
    assert "function fmtDuration" in resp.text
    assert "white-space: nowrap" in resp.text
