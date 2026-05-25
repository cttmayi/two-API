import json
import pytest
import httpx
from fastapi import FastAPI
from src.config import ModelEntry
from src.router import ModelRouter
from src.forwarder import set_forward_client, reset_forward_client


@pytest.fixture(autouse=True)
def reset_client():
    yield
    reset_forward_client()


@pytest.fixture
def app_with_models():
    """Create a FastAPI app with test models, bypassing config.yaml."""
    from fastapi import FastAPI
    from src.handlers.openai import router as openai_router
    from src.handlers.anthropic import router as anthropic_router

    app = FastAPI()
    models = [
        ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-test"),
        ModelEntry(
            names=["deepseek-chat"],
            openai_base_url="https://api.deepseek.com",
            anthropic_base_url="https://api.deepseek.com/anthropic",
            api_key="sk-ds",
        ),
        ModelEntry(names=["claude-sonnet-4-6"], anthropic_base_url="https://api.anthropic.com", api_key="sk-ant"),
    ]
    app.state.router = ModelRouter(models)
    app.include_router(openai_router)
    app.include_router(anthropic_router)
    return app


@pytest.fixture
def client(app_with_models):
    transport = httpx.ASGITransport(app=app_with_models)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return client


class TestOpenAIEndpoints:
    @pytest.mark.asyncio
    async def test_chat_completions_non_stream(self, client):
        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["model"] == "gpt-4o"
            assert request.headers["authorization"] == "Bearer sk-test"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_chat_completions_missing_model(self, client):
        resp = await client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 400
        assert "model" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_chat_completions_unknown_model(self, client):
        resp = await client.post("/v1/chat/completions", json={
            "model": "nonexistent",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 404
        assert "unknown" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_chat_completions_wrong_endpoint(self, client):
        """claude-sonnet-4-6 has no openai_base_url, so requesting via OpenAI endpoint should fail."""
        resp = await client.post("/v1/chat/completions", json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 404
        assert "not available" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_chat_completions_backend_unreachable(self, client):
        """Simulate connection error by using a transport that raises ConnectError."""
        def failing_handler(request):
            raise httpx.ConnectError("Connection refused")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
        set_forward_client(mock_client)

        resp = await client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 502
        assert "unreachable" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_chat_completions_backend_error_passthrough(self, client):
        async def backend_handler(request):
            return httpx.Response(500, json={"error": "internal server error"})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 500
        assert "internal server error" in resp.text

    @pytest.mark.asyncio
    async def test_list_models(self, client):
        resp = await client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        model_ids = [m["id"] for m in data["data"]]
        assert "gpt-4o" in model_ids
        assert "deepseek-chat" in model_ids
        assert "claude-sonnet-4-6" not in model_ids  # no openai_base_url


class TestAnthropicEndpoints:
    @pytest.mark.asyncio
    async def test_messages_non_stream(self, client):
        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["model"] == "claude-sonnet-4-6"
            assert request.headers["authorization"] == "Bearer sk-ant"
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/v1/messages", json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"][0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_messages_wrong_endpoint(self, client):
        """gpt-4o has no anthropic_base_url, so requesting via Anthropic endpoint should fail."""
        resp = await client.post("/v1/messages", json={
            "model": "gpt-4o",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 404
        assert "not available" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_messages_dual_format_model(self, client):
        """deepseek-chat has both base_urls, so it should work on Anthropic endpoint."""
        async def backend_handler(request):
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "from deepseek"}],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/v1/messages", json={
            "model": "deepseek-chat",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"][0]["text"] == "from deepseek"


class TestStreaming:
    @pytest.mark.asyncio
    async def test_openai_streaming(self, client):
        chunks = [
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n',
            b'data: [DONE]\n\n',
        ]

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["stream"] is True

            async def stream():
                for chunk in chunks:
                    yield chunk
            return httpx.Response(200, content=stream(), headers={"content-type": "text/event-stream"})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/v1/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        assert resp.status_code == 200
        body = resp.content
        for chunk in chunks:
            assert chunk in body
