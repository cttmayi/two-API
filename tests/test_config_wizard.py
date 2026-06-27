import httpx
import pytest
from fastapi import FastAPI
from src.config import Config, ModelEntry
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
    assert data["models"][0]["api_key"] == "sk-****"


@pytest.mark.asyncio
async def test_get_config_masks_api_key():
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
    assert data["models"][0]["api_key"] == "sk-****"
    assert "real-key" not in data["models"][0]["api_key"]


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
