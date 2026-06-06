import json
import pytest
import httpx
from fastapi import FastAPI
from src.config import ModelEntry
from src.router import ModelRouter
from src.forwarder import set_forward_client, reset_forward_client
from src.config import Config
from src.stats import get_stats


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
    app.state.config = Config(models=models)
    app.include_router(openai_router)
    app.include_router(anthropic_router)
    return app


@pytest.fixture
def client(app_with_models):
    transport = httpx.ASGITransport(app=app_with_models)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


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

        resp = await client.post("/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "hello"

    @pytest.mark.asyncio
    async def test_chat_completions_missing_model(self, client):
        resp = await client.post("/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 400
        assert "model" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_chat_completions_unknown_model(self, client):
        resp = await client.post("/chat/completions", json={
            "model": "nonexistent",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 404
        assert "unknown" in resp.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_chat_completions_global_alias(self, client, app_with_models):
        """Global alias should rewrite model before routing."""
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["gpt-4o-mini"], openai_base_url="https://api.openai.com", api_key="sk-test"),
            ],
            alias={"default": "gpt-4o-mini"},
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["model"] == "gpt-4o-mini", f"Expected gpt-4o-mini, got {body['model']}"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/chat/completions", json={
            "model": "default",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "ok"

    @pytest.mark.asyncio
    async def test_chat_completions_max_tokens_default(self, client, app_with_models):
        """When client omits max_tokens and entry has max_tokens set, inject default."""
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com",
                           api_key="sk-test", max_tokens=4096),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["max_tokens"] == 4096, "Should inject default max_tokens"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_completions_max_tokens_client_override(self, client, app_with_models):
        """When client provides max_tokens, entry's default should not override."""
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com",
                           api_key="sk-test", max_tokens=4096),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["max_tokens"] == 2048, "Client value should be preserved"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 2048,
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_completions_wrong_endpoint(self, client):
        """claude-sonnet-4-6 has no openai_base_url, so requesting via OpenAI endpoint should fail."""
        resp = await client.post("/chat/completions", json={
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

        resp = await client.post("/chat/completions", json={
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

        resp = await client.post("/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 500
        assert "internal server error" in resp.text

    @pytest.mark.asyncio
    async def test_responses_non_stream(self, client):
        async def backend_handler(request):
            assert request.url.path == "/responses"
            body = json.loads(request.content)
            assert body["model"] == "gpt-4o"
            assert body["input"] == "hello"
            assert request.headers["authorization"] == "Bearer sk-test"
            return httpx.Response(200, json={
                "id": "resp_123",
                "output_text": "hi there",
                "usage": {"input_tokens": 7, "output_tokens": 3},
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "gpt-4o", "input": "hello"})
        assert resp.status_code == 200
        assert resp.json()["output_text"] == "hi there"

    @pytest.mark.asyncio
    async def test_v1_responses_non_stream(self, client):
        async def backend_handler(request):
            assert request.url.path == "/v1/responses"
            return httpx.Response(200, json={"output_text": "ok"})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/v1/responses", json={"model": "gpt-4o", "input": "hello"})
        assert resp.status_code == 200
        assert resp.json()["output_text"] == "ok"

    @pytest.mark.asyncio
    async def test_responses_alias_and_max_output_tokens_default(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["gpt-4o-mini"], openai_base_url="https://api.openai.com",
                           api_key="sk-test", max_tokens=4096),
            ],
            alias={"default": "gpt-4o-mini"},
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["model"] == "gpt-4o-mini"
            assert body["max_output_tokens"] == 4096
            return httpx.Response(200, json={"output_text": "ok"})

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "default", "input": "hello"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_responses_to_chat_non_stream(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(
                    names=["resp-chat"],
                    openai_base_url="https://api.openai.com",
                    api_key="sk-test",
                    responses_to_chat=True,
                ),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            assert request.url.path == "/chat/completions"
            body = json.loads(request.content)
            assert body["model"] == "resp-chat"
            assert body["messages"] == [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hello"},
            ]
            assert body["max_tokens"] == 12
            assert body["temperature"] == 0.7
            assert "input" not in body
            assert "max_output_tokens" not in body
            return httpx.Response(200, json={
                "id": "chatcmpl_123",
                "created": 1234567890,
                "model": "resp-chat",
                "choices": [{
                    "message": {"role": "assistant", "content": "hi there"},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 3,
                    "total_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": 2},
                },
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={
            "model": "resp-chat",
            "instructions": "You are helpful.",
            "input": "hello",
            "max_output_tokens": 12,
            "temperature": 0.7,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "response"
        assert data["status"] == "completed"
        assert data["output_text"] == "hi there"
        assert data["output"][0]["content"][0]["text"] == "hi there"
        assert data["usage"] == {
            "input_tokens": 7,
            "output_tokens": 3,
            "total_tokens": 10,
            "input_tokens_details": {"cached_tokens": 2},
        }

    @pytest.mark.asyncio
    async def test_responses_to_chat_converts_chat_tool_calls_to_responses_output(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            return httpx.Response(200, json={
                "id": "chatcmpl_123",
                "created": 1234567890,
                "model": "resp-chat",
                "choices": [{"message": {
                    "role": "assistant",
                    "content": "I will run it.",
                    "tool_calls": [{
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "exec_command", "arguments": "{\"cmd\":\"pwd\"}"},
                    }],
                }}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "resp-chat", "input": "hello"})

        assert resp.status_code == 200
        assert resp.json()["output"][1] == {
            "id": "call_123",
            "type": "function_call",
            "call_id": "call_123",
            "name": "exec_command",
            "arguments": "{\"cmd\":\"pwd\"}",
            "status": "completed",
        }

    @pytest.mark.asyncio
    async def test_responses_to_chat_forwards_function_tools(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["tools"] == [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather by city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }]
            return httpx.Response(200, json={
                "id": "chatcmpl_123",
                "created": 1234567890,
                "model": "resp-chat",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={
            "model": "resp-chat",
            "input": "hello",
            "tools": [{
                "type": "function",
                "name": "get_weather",
                "description": "Get weather by city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }],
        })

        assert resp.status_code == 200
        assert resp.json()["output_text"] == "hi"

    @pytest.mark.asyncio
    async def test_responses_to_chat_normalizes_codex_messages_and_tools(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["messages"] == [
                {"role": "system", "content": "rules"},
                {"role": "user", "content": "hello"},
            ]
            assert body["tools"] == [{
                "type": "function",
                "function": {
                    "name": "exec_command",
                    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
                },
            }]
            return httpx.Response(200, json={
                "id": "chatcmpl_123",
                "created": 1234567890,
                "model": "resp-chat",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={
            "model": "resp-chat",
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": "rules"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            ],
            "tools": [
                {"type": "function", "name": "exec_command", "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}},
                {"type": "namespace", "name": "multi_agent_v1", "tools": []},
                {"type": "web_search"},
            ],
        })

        assert resp.status_code == 200
        assert resp.json()["output_text"] == "ok"

    @pytest.mark.asyncio
    async def test_responses_to_chat_records_empty_backend_error_detail(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            return httpx.Response(400, content=b"")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "resp-chat", "input": "hello"})

        assert resp.status_code == 400
        recent = get_stats().snapshot()["recent"][0]
        assert recent["output"]["backend_status"] == 400
        assert recent["output"]["converted_request"]["messages"] == [{"role": "user", "content": "hello"}]

    @pytest.mark.asyncio
    async def test_responses_to_chat_streaming_records_empty_backend_error_detail(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            return httpx.Response(400, content=b"")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "resp-chat", "input": "hello", "stream": True})

        assert resp.status_code == 200
        recent = get_stats().snapshot()["recent"][0]
        assert recent["status"] == 400
        assert recent["output"]["backend_status"] == 400
        assert recent["output"]["converted_request"]["stream"] is True
        assert recent["output"]["converted_request"]["messages"] == [{"role": "user", "content": "hello"}]

    @pytest.mark.asyncio
    async def test_responses_to_chat_streaming(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            assert request.url.path == "/chat/completions"
            body = json.loads(request.content)
            assert body["stream"] is True
            assert body["messages"] == [{"role": "user", "content": "hello"}]
            return httpx.Response(200, content=(
                'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
                'data: {"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n'
                'data: [DONE]\n\n'
            ))

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "resp-chat", "input": "hello", "stream": True})

        assert resp.status_code == 200
        assert "response.output_item.added" in resp.text
        assert "response.content_part.added" in resp.text
        assert "response.output_text.delta" in resp.text
        assert '"delta":"he"' in resp.text
        assert '"delta":"llo"' in resp.text
        assert "response.output_text.done" in resp.text
        assert "response.content_part.done" in resp.text
        assert "response.output_item.done" in resp.text
        assert "response.completed" in resp.text

    @pytest.mark.asyncio
    async def test_responses_to_chat_streaming_converts_tool_calls(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            return httpx.Response(200, content=(
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"exec_command","arguments":"{\\\"cmd\\\":\\\"pwd"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\\"}"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
                'data: [DONE]\n\n'
            ))

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "resp-chat", "input": "hello", "stream": True})

        assert resp.status_code == 200
        assert "response.output_item.done" in resp.text
        assert '"type":"function_call"' in resp.text
        assert '"call_id":"call_123"' in resp.text
        assert '"name":"exec_command"' in resp.text
        assert '"arguments":"{\\"cmd\\":\\"pwd\\"}"' in resp.text

    @pytest.mark.asyncio
    async def test_responses_to_chat_streaming_uses_zero_usage_when_missing(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            return httpx.Response(200, content=(
                'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                'data: [DONE]\n\n'
            ))

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "resp-chat", "input": "hello", "stream": True})

        assert resp.status_code == 200
        assert '"usage":{"input_tokens":0,"output_tokens":0,"total_tokens":0}' in resp.text
        assert "null" not in resp.text

    @pytest.mark.asyncio
    async def test_responses_to_chat_rejects_previous_response_id(self, client, app_with_models):
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["resp-chat"], openai_base_url="https://api.openai.com", responses_to_chat=True),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        resp = await client.post("/responses", json={
            "model": "resp-chat",
            "input": "hello",
            "previous_response_id": "resp_123",
        })

        assert resp.status_code == 400
        assert "previous_response_id" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_responses_streaming(self, client):
        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["stream"] is True
            return httpx.Response(200, content=(
                'data: {"type":"response.output_text.delta","delta":"he"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"llo"}\n\n'
                'data: {"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}\n\n'
            ))

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/responses", json={"model": "gpt-4o", "input": "hi", "stream": True})
        assert resp.status_code == 200
        assert "response.output_text.delta" in resp.text

    @pytest.mark.asyncio
    async def test_list_models(self, client):
        resp = await client.get("/models")
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

        resp = await client.post("/messages", json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["content"][0]["text"] == "hello"

    @pytest.mark.asyncio
    async def test_messages_max_tokens_default(self, client, app_with_models):
        """When client omits max_tokens and entry has max_tokens set, inject default."""
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["claude-sonnet-4-6"], anthropic_base_url="https://api.anthropic.com",
                           api_key="sk-ant", max_tokens=8192),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["max_tokens"] == 8192, "Should inject default max_tokens"
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "ok"}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/messages", json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_messages_max_tokens_client_override(self, client, app_with_models):
        """When client provides max_tokens, entry's default should not override."""
        app_with_models.state.config = Config(
            models=[
                ModelEntry(names=["claude-sonnet-4-6"], anthropic_base_url="https://api.anthropic.com",
                           api_key="sk-ant", max_tokens=8192),
            ],
        )
        app_with_models.state.router = ModelRouter(app_with_models.state.config.models)

        async def backend_handler(request):
            body = json.loads(request.content)
            assert body["max_tokens"] == 4096, "Client value should be preserved"
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "ok"}],
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        resp = await client.post("/messages", json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_messages_wrong_endpoint(self, client):
        """gpt-4o has no anthropic_base_url, so requesting via Anthropic endpoint should fail."""
        resp = await client.post("/messages", json={
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

        resp = await client.post("/messages", json={
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

        resp = await client.post("/chat/completions", json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        assert resp.status_code == 200
        body = resp.content
        for chunk in chunks:
            assert chunk in body
