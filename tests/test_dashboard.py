import httpx
import pytest

from src.config import Config, LoggingConfig, ModelEntry
from src.main import app, usage_path_for_log_dir
from src.router import ModelRouter
from src.stats import get_stats


@pytest.fixture(autouse=True)
def reset_stats():
    from src import stats
    stats._stats = None
    yield
    stats._stats = None


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
    assert '<div class="section-title" style="cursor:pointer; user-select:none;" onclick="toggleSection(\'hourly-body\')">' in resp.text
    assert "hourly-title" not in resp.text
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
    assert "Tokens/s" in resp.text
    assert "Cache Read: ' + fmtMetric(item.cache_read_tokens)" in resp.text
    assert "Cache Write: ' + fmtMetric(item.cache_write_tokens)" in resp.text
    assert "CR ' + fmtMetric(data.cache_read_tokens)" in resp.text
    assert "CW ' + fmtMetric(data.cache_write_tokens)" in resp.text
    assert "function fmtDuration" in resp.text
    assert "max-width: 840px" in resp.text
    assert "white-space: nowrap" in resp.text
    assert "overflow-wrap: anywhere" not in resp.text


@pytest.mark.asyncio
async def test_homepage_preserves_empty_hourly_chart_gaps():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    stats = get_stats()
    stats._hourly = {
        "2026-06-07 10:00": {
            "hour": "2026-06-07 10:00",
            "requests": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 15,
            "total_latency_ms": 100,
            "aliases": {
                "": {
                    "gpt-4o": {
                        "provider": "openai",
                        "requests": 1,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "total_tokens": 15,
                        "total_latency_ms": 100,
                    }
                },
            },
        },
        "2026-06-07 12:00": {
            "hour": "2026-06-07 12:00",
            "requests": 1,
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 30,
            "total_latency_ms": 120,
            "aliases": {
                "": {
                    "gpt-4o": {
                        "provider": "openai",
                        "requests": 1,
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "total_tokens": 30,
                        "total_latency_ms": 120,
                    }
                },
            },
        },
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert '"hour": "2026-06-07 10:00"' in resp.text
    assert '"hour": "2026-06-07 11:00"' in resp.text
    assert '"hour": "2026-06-07 12:00"' in resp.text
    assert '"hour": "2026-06-07 11:00", "requests": 0' in resp.text


@pytest.mark.asyncio
async def test_homepage_renders_hourly_group_by_model_or_alias_control():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o-mini"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    get_stats().record("gpt-4o-mini", "openai", prompt_tokens=10, completion_tokens=5, latency_ms=100, alias="default")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert "hourly-group-by" in resp.text
    assert "Group by" in resp.text
    assert '<option value="aliases" selected>ALIAS</option>' in resp.text
    assert '<option value="models">MODEL</option>' in resp.text
    assert "function setHourlyGroup" in resp.text
    assert "function hourlyGroups" in resp.text
    assert "metricValue(item," in resp.text
    assert "hourlyGroupBy === \"aliases\" && !Object.keys(groups).length" not in resp.text
    assert "default" in resp.text


@pytest.mark.asyncio
async def test_homepage_renders_24_hour_grouped_usage_detail():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o", "gpt-4o-mini"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    stats = get_stats()
    stats._hourly = {
        "2026-06-07 10:00": {
            "hour": "2026-06-07 10:00",
            "requests": 1,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cache_read_tokens": 1,
            "cache_write_tokens": 2,
            "total_tokens": 15,
            "total_latency_ms": 100,
            "aliases": {
                "": {
                    "gpt-4o": {
                        "provider": "openai",
                        "requests": 1,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "cache_read_tokens": 1,
                        "cache_write_tokens": 2,
                        "total_tokens": 15,
                        "total_latency_ms": 100,
                    }
                },
            },
        },
        "2026-06-07 11:00": {
            "hour": "2026-06-07 11:00",
            "requests": 1,
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "cache_read_tokens": 3,
            "cache_write_tokens": 4,
            "total_tokens": 30,
            "total_latency_ms": 200,
            "aliases": {
                "fast": {
                    "gpt-4o-mini": {
                        "provider": "openai",
                        "requests": 1,
                        "prompt_tokens": 20,
                        "completion_tokens": 10,
                        "cache_read_tokens": 3,
                        "cache_write_tokens": 4,
                        "total_tokens": 30,
                        "total_latency_ms": 200,
                    }
                },
            },
        },
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert "function groupedUsageSummary" in resp.text
    assert "function renderHourlyDetail" in resp.text
    assert "24-Hour Usage by ' + (hourlyGroupBy === \"aliases\" ? \"Alias\" : \"Model\")" not in resp.text
    assert "hourly-detail-table-wrap" in resp.text
    assert "recent-table-wrap hourly-detail-table-wrap" in resp.text
    assert "recent-table hourly-detail-table" in resp.text
    assert "Cache Read" in resp.text
    assert "Cache Write" in resp.text
    assert "Average Latency" in resp.text
    assert "Tokens/s" in resp.text
    assert "hourly-detail-row" in resp.text
    assert "hourly-detail-name" in resp.text
    assert "Object.keys(rows).sort().map(function(key)" in resp.text
    assert "showHourlyDetail(hourlyUsageData[hourlyUsageData.length - 1])" not in resp.text
    assert "renderHourlyDetail();" in resp.text
    assert "setHourlyGroup(this.value)" in resp.text


@pytest.mark.asyncio
async def test_recent_requests_renders_and_downloads_alias_field():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o-mini"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    get_stats().record_detail(
        model="gpt-4o-mini",
        alias="default",
        provider="openai",
        streaming=False,
        latency_ms=123,
        status=200,
        prompt_tokens=1,
        completion_tokens=2,
        cache_read=None,
        cache_write=None,
        input_messages=[{"role": "user", "content": "hello"}],
        output_content={"content": "ok"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")
        download = await client.get("/recent/download?i=0")

    assert page.status_code == 200
    assert "<th>Time</th><th>Alias</th><th>Model</th>" in page.text
    assert '<th class="cell-num">Latency</th>' in page.text
    assert '<th class="cell-num">Prompt</th>' in page.text
    assert '<th class="cell-num">Completion</th>' in page.text
    assert '<th class="cell-num">Cache Read</th>' in page.text
    assert '<th class="cell-num">Cache Write</th>' in page.text
    assert ".recent-table th.cell-num" in page.text
    assert "default" in page.text
    assert "colspan=\"14\"" in page.text
    assert download.status_code == 200
    data = download.json()
    assert data["model"] == "gpt-4o-mini"
    assert data["alias"] == "default"


@pytest.mark.asyncio
async def test_recent_requests_renders_output_text_value_without_key():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    get_stats().record_detail(
        model="gpt-4o",
        provider="openai",
        streaming=False,
        latency_ms=123,
        status=200,
        prompt_tokens=1,
        completion_tokens=2,
        cache_read=None,
        cache_write=None,
        input_messages=[{"role": "user", "content": "hello"}],
        output_content={"output_text": "plain answer"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/")

    assert page.status_code == 200
    assert "plain answer" in page.text
    assert "output_text=plain answer" not in page.text


@pytest.mark.asyncio
async def test_homepage_renders_structured_error_output_preview():
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    get_stats().record_detail(
        model="gpt-4o",
        provider="openai",
        streaming=False,
        latency_ms=123,
        status=400,
        prompt_tokens=None,
        completion_tokens=None,
        cache_read=None,
        cache_write=None,
        input_messages=[{"role": "user", "content": "hello"}],
        output_content={"backend_status": 400, "backend_error": "empty response body"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    # Error data is in the hidden JSON detail section, not in the preview
    assert 'backend_status' in resp.text and '400' in resp.text
    assert 'backend_error' in resp.text and 'empty response body' in resp.text
