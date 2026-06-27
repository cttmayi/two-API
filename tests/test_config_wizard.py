import os
import httpx
import pytest
import yaml
from fastapi import FastAPI
from src.config import Config, ModelEntry
from src.cache import reset_cache, init_cache, CacheConfig, get_cache
from src.router import ModelRouter


@pytest.mark.asyncio
async def test_get_config_returns_config_json():
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-real-key-12345")],
        alias={"default": "gpt-4o"},
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = "/tmp/test-two-api-config.yaml"
    from src.main import config_router
    app.include_router(config_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["server"]["host"] == "0.0.0.0"
    assert data["server"]["port"] == 8080
    assert len(data["models"]) == 1
    assert data["models"][0]["names"] == ["gpt-4o"]
    assert data["models"][0]["api_key"] == "sk-real-key-12345"


@pytest.mark.asyncio
async def test_get_config_returns_raw_api_key():
    """API keys are returned as-is, not masked."""
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-real-key-12345")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = "/tmp/test-two-api-config.yaml"
    from src.main import config_router
    app.include_router(config_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"][0]["api_key"] == "sk-real-key-12345"


@pytest.mark.asyncio
async def test_get_config_handles_no_api_key():
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = "/tmp/test-two-api-config.yaml"
    from src.main import config_router
    app.include_router(config_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["models"][0]["api_key"] is None


@pytest.mark.asyncio
async def test_post_config_writes_file_and_updates_state(tmp_path):
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-old-key")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    config_path = str(tmp_path / "config.yaml")
    app.state.config_path = config_path
    init_cache(CacheConfig(enabled=True))

    from src.main import config_router
    app.include_router(config_router)

    new_config = {
        "server": {"host": "127.0.0.1", "port": 9000},
        "models": [
            {
                "names": ["claude-opus-4"],
                "anthropic_base_url": "https://api.anthropic.com/v1",
                "api_key": "sk-ant-new-key",
                "max_tokens": None,
                "responses_to_chat": False,
            }
        ],
        "alias": {},
        "logging": {"level": "DEBUG", "output": "console", "dir": "~/.two-api/logs"},
        "cache": {"enabled": False, "ttl_seconds": 3600, "max_entries": 2000, "aliases": [], "key_fields": []},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/config", json=new_config)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # Verify app.state updated
    assert app.state.config.server.host == "127.0.0.1"
    assert app.state.config.server.port == 9000
    assert app.state.config.models[0].api_key == "sk-ant-new-key"

    # Verify YAML file written
    assert os.path.exists(config_path)
    with open(config_path) as f:
        saved = yaml.safe_load(f)
    assert saved["server"]["host"] == "127.0.0.1"
    assert saved["models"][0]["api_key"] == "sk-ant-new-key"

    # Verify router updated
    match = app.state.router.match("claude-opus-4", "anthropic")
    assert match is not None

    # Verify cache was re-initialized (enabled=False now)
    cache = get_cache()
    assert cache.enabled is False


@pytest.mark.asyncio
async def test_post_config_with_openai_base_url(tmp_path):
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = str(tmp_path / "config2.yaml")

    from src.main import config_router
    app.include_router(config_router)

    new_config = {
        "server": {"host": "0.0.0.0", "port": 8080},
        "models": [
            {
                "names": ["gpt-4o", {"fast": "gpt-4o-mini"}],
                "openai_base_url": "https://api.openai.com/v1",
                "api_key": None,
                "max_tokens": 4096,
                "responses_to_chat": True,
            }
        ],
        "alias": {"fast": "gpt-4o-mini"},
        "logging": {"level": "INFO", "output": "file", "dir": "~/.two-api/logs"},
        "cache": {"enabled": True, "ttl_seconds": 7200, "max_entries": 1000, "aliases": ["fast"], "key_fields": ["model"]},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/config", json=new_config)

    assert resp.status_code == 200
    assert app.state.config.models[0].max_tokens == 4096
    assert app.state.config.models[0].responses_to_chat is True
    assert app.state.config.cache.ttl_seconds == 7200
    assert app.state.config.cache.max_entries == 1000


@pytest.mark.asyncio
async def test_post_config_preserves_masked_api_key(tmp_path):
    """Sending a masked api_key should preserve the original key from current config."""
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-original-secret")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = str(tmp_path / "config3.yaml")

    from src.main import config_router
    app.include_router(config_router)

    new_config = {
        "server": {"host": "0.0.0.0", "port": 8080},
        "models": [{"names": ["gpt-4o"], "openai_base_url": "https://api.openai.com/v1", "api_key": "sk-****"}],
        "alias": {},
        "logging": {"level": "INFO", "output": "file", "dir": "~/.two-api/logs"},
        "cache": {"enabled": True, "ttl_seconds": 3600, "max_entries": 2000, "aliases": [], "key_fields": []},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/config", json=new_config)

    assert resp.status_code == 200
    assert app.state.config.models[0].api_key == "sk-original-secret"


@pytest.mark.asyncio
async def test_post_config_replaces_api_key_when_new_value_given(tmp_path):
    """Sending a non-masked api_key should replace the current key."""
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-old-key")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = str(tmp_path / "config4.yaml")

    from src.main import config_router
    app.include_router(config_router)

    new_config = {
        "server": {"host": "0.0.0.0", "port": 8080},
        "models": [{"names": ["gpt-4o"], "openai_base_url": "https://api.openai.com/v1", "api_key": "sk-brand-new-key"}],
        "alias": {},
        "logging": {"level": "INFO", "output": "file", "dir": "~/.two-api/logs"},
        "cache": {"enabled": True, "ttl_seconds": 3600, "max_entries": 2000, "aliases": [], "key_fields": []},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/config", json=new_config)

    assert resp.status_code == 200
    assert app.state.config.models[0].api_key == "sk-brand-new-key"


@pytest.mark.asyncio
async def test_post_config_invalid_data_returns_422(tmp_path):
    """Missing required 'models' field should return 422."""
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = str(tmp_path / "bad1.yaml")

    from src.main import config_router
    app.include_router(config_router)

    bad_config = {"server": {"host": "0.0.0.0", "port": 8080}}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/config", json=bad_config)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_settings_page_renders():
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = "/tmp/test-settings.yaml"

    from src.main import config_router
    app.include_router(config_router)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/settings")

    assert resp.status_code == 200
    assert "Settings" in resp.text
    assert "Server" in resp.text or "server" in resp.text.lower()
    assert "Models" in resp.text or "models" in resp.text.lower()
    assert "Save" in resp.text


@pytest.mark.asyncio
async def test_post_config_invalid_model_no_base_url_returns_422(tmp_path):
    """Model entry without any base_url should return 422."""
    app = FastAPI()
    app.state.config = Config(
        models=[ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com")],
    )
    app.state.router = ModelRouter(app.state.config.models)
    app.state.config_path = str(tmp_path / "bad2.yaml")

    from src.main import config_router
    app.include_router(config_router)

    bad_config = {
        "server": {"host": "0.0.0.0", "port": 8080},
        "models": [{"names": ["invalid-model"]}],
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/config", json=bad_config)

    assert resp.status_code == 422
