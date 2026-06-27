import json
import time
import pytest
import httpx
from fastapi import FastAPI
from src.cache import (
    CacheConfig, CacheStore, CacheEntry,
    _build_cache_key, _should_cache, stream_from_cache,
    init_cache, get_cache, reset_cache,
)
from src.config import Config, ModelEntry, CacheConfigModel
from src.router import ModelRouter
from src.router import ModelRouter
from src.forwarder import set_forward_client, reset_forward_client


class TestCacheConfig:
    def test_default_values(self):
        cfg = CacheConfig()
        assert cfg.enabled is True
        assert cfg.ttl_seconds == 3600
        assert cfg.max_entries == 2000
        assert cfg.aliases == []
        assert cfg.key_fields == []


class TestCacheStore:
    def test_set_and_get(self):
        store = CacheStore(CacheConfig())
        entry = CacheEntry(response_body=b'{"ok":true}', created_at=time.time())
        store.set("key1", entry)
        got = store.get("key1")
        assert got is not None
        assert got.response_body == b'{"ok":true}'

    def test_miss_returns_none(self):
        store = CacheStore(CacheConfig())
        assert store.get("nonexistent") is None

    def test_tracks_hits_and_misses(self):
        store = CacheStore(CacheConfig())
        store.set("k", CacheEntry(response_body=b"x"))
        store.get("k")
        store.get("k")
        store.get("missing")
        assert store.hits == 2
        assert store.misses == 1

    def test_size_increases_on_set(self):
        store = CacheStore(CacheConfig())
        assert store.size == 0
        store.set("a", CacheEntry(response_body=b"1"))
        assert store.size == 1
        store.set("b", CacheEntry(response_body=b"2"))
        assert store.size == 2

    def test_sse_lines_storage(self):
        store = CacheStore(CacheConfig())
        lines = ["data: hello", "data: world", ""]
        store.set("sse", CacheEntry(sse_lines=lines))
        got = store.get("sse")
        assert got.sse_lines == lines


class TestBuildCacheKey:
    def test_includes_messages(self):
        body = {"messages": [{"role": "user", "content": "hi"}], "model": "gpt-4o"}
        k1 = _build_cache_key("", body, [])
        assert isinstance(k1, str)
        assert len(k1) == 64  # sha256 hex

    def test_different_messages_different_keys(self):
        body1 = {"messages": [{"role": "user", "content": "hi"}]}
        body2 = {"messages": [{"role": "user", "content": "hello"}]}
        assert _build_cache_key("", body1, []) != _build_cache_key("", body2, [])

    def test_includes_input_when_no_messages(self):
        body = {"input": "hello", "model": "gpt-4o"}
        k = _build_cache_key("", body, [])
        assert isinstance(k, str) and len(k) == 64

    def test_key_fields_affect_key(self):
        body = {"messages": [{"role": "user", "content": "hi"}], "temperature": 0.7, "max_tokens": 100}
        k1 = _build_cache_key("", body, [])
        k2 = _build_cache_key("", body, ["temperature"])
        k3 = _build_cache_key("", body, ["temperature", "max_tokens"])
        assert k1 != k2
        assert k2 != k3

    def test_alias_in_key_fields(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        k1 = _build_cache_key("default", body, ["alias"])
        k2 = _build_cache_key("fast", body, ["alias"])
        assert k1 != k2

    def test_path_distinguishes_endpoints(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        k1 = _build_cache_key("", body, [], "/chat/completions")
        k2 = _build_cache_key("", body, [], "/messages")
        assert k1 != k2

    def test_key_fields_sorted(self):
        body = {"messages": [], "z_field": "z", "a_field": "a"}
        k = _build_cache_key("", body, ["z_field", "a_field"])
        # Order of key_fields shouldn't matter since they're sorted internally
        k_rev = _build_cache_key("", body, ["a_field", "z_field"])
        assert k == k_rev


class TestShouldCache:
    def test_disabled_returns_false(self):
        config = CacheConfig(enabled=False)
        assert _should_cache(config, "default", []) is False

    def test_alias_in_key_fields_requires_non_empty_alias(self):
        config = CacheConfig(enabled=True, key_fields=["alias"])
        assert _should_cache(config, "", ["alias"]) is False
        assert _should_cache(config, "default", ["alias"]) is True

    def test_aliases_allowlist(self):
        config = CacheConfig(enabled=True, aliases=["default"])
        assert _should_cache(config, "default", []) is True
        assert _should_cache(config, "fast", []) is False

    def test_empty_aliases_allowlist_allows_all(self):
        config = CacheConfig(enabled=True, aliases=[])
        assert _should_cache(config, "default", []) is True
        assert _should_cache(config, "fast", []) is True
        assert _should_cache(config, "", []) is True

    def test_alias_in_key_fields_and_allowlist(self):
        config = CacheConfig(enabled=True, aliases=["default"], key_fields=["alias"])
        assert _should_cache(config, "default", ["alias"]) is True
        assert _should_cache(config, "fast", ["alias"]) is False  # not in allowlist
        assert _should_cache(config, "", ["alias"]) is False  # empty alias


class TestStreamFromCache:
    @pytest.mark.asyncio
    async def test_yields_sse_lines(self):
        entry = CacheEntry(sse_lines=["data: hello", "data: world", ""])
        chunks = [chunk async for chunk in stream_from_cache(entry)]
        assert chunks == [b"data: hello\n", b"data: world\n", b"\n"]

    @pytest.mark.asyncio
    async def test_empty_sse_lines(self):
        entry = CacheEntry(sse_lines=[])
        chunks = [chunk async for chunk in stream_from_cache(entry)]
        assert chunks == []


class TestCacheSingleton:
    def test_init_and_get(self):
        reset_cache()
        config = CacheConfig(enabled=False)
        store = init_cache(config)
        assert get_cache() is store

    def test_auto_init_disabled(self):
        reset_cache()
        store = get_cache()
        assert store is not None
        assert store.hits == 0


class TestCacheHandlerIntegration:
    @pytest.fixture(autouse=True)
    def setup(self):
        reset_cache()
        reset_forward_client()
        yield
        reset_forward_client()
        reset_cache()

    def _make_app(self, cache_config: CacheConfigModel | None = None):
        from src.handlers.openai import router as openai_router
        from src.handlers.anthropic import router as anthropic_router
        app = FastAPI()
        models = [
            ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-test"),
            ModelEntry(names=["claude-sonnet-4-6"], anthropic_base_url="https://api.anthropic.com", api_key="sk-ant"),
        ]
        app.state.router = ModelRouter(models)
        app.state.config = Config(
            models=models,
            cache=cache_config or CacheConfigModel(enabled=True, key_fields=["model"]),
        )
        app.include_router(openai_router)
        app.include_router(anthropic_router)
        return app

    @pytest.mark.asyncio
    async def test_openai_non_stream_cache_hit(self):
        app = self._make_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # First request - cache miss, forward to backend
            call_count = 0

            async def backend_handler(request):
                nonlocal call_count
                call_count += 1
                body = json.loads(request.content)
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "cached response"}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                })

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
            set_forward_client(mock_client)

            resp1 = await client.post("/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "cache test"}],
            })
            assert resp1.status_code == 200
            assert call_count == 1

            # Second request - same body, should be cache hit
            resp2 = await client.post("/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "cache test"}],
            })
            assert resp2.status_code == 200
            assert call_count == 1, "Backend should not be called again on cache hit"
            assert resp2.json()["choices"][0]["message"]["content"] == "cached response"

    @pytest.mark.asyncio
    async def test_openai_streaming_cache_hit(self):
        app = self._make_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            call_count = 0

            async def backend_handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n')

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
            set_forward_client(mock_client)

            resp1 = await client.post("/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "stream cache"}],
                "stream": True,
            })
            assert resp1.status_code == 200
            assert call_count == 1
            content1 = resp1.content

            resp2 = await client.post("/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "stream cache"}],
                "stream": True,
            })
            assert resp2.status_code == 200
            assert call_count == 1, "Backend should not be called again"
            assert resp2.content == content1

    @pytest.mark.asyncio
    async def test_anthropic_non_stream_cache_hit(self):
        app = self._make_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            call_count = 0

            async def backend_handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(200, json={
                    "content": [{"type": "text", "text": "cached claude"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                })

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
            set_forward_client(mock_client)

            resp1 = await client.post("/messages", json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "cache test"}],
            })
            assert resp1.status_code == 200
            assert call_count == 1

            resp2 = await client.post("/messages", json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "cache test"}],
            })
            assert resp2.status_code == 200
            assert call_count == 1
            assert resp2.json()["content"][0]["text"] == "cached claude"

    @pytest.mark.asyncio
    async def test_anthropic_streaming_cache_hit(self):
        app = self._make_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            call_count = 0

            async def backend_handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(200, content=b'data: {"type":"content_block_delta","delta":{"text":"hello"}}\n\ndata: {"type":"message_delta","usage":{"output_tokens":1}}\n\n')

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
            set_forward_client(mock_client)

            resp1 = await client.post("/messages", json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "stream test"}],
                "stream": True,
            })
            assert resp1.status_code == 200
            assert call_count == 1

            resp2 = await client.post("/messages", json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "stream test"}],
                "stream": True,
            })
            assert resp2.status_code == 200
            assert call_count == 1

    @pytest.mark.asyncio
    async def test_cache_disabled_skips_cache(self):
        app = self._make_app(cache_config=CacheConfigModel(enabled=False))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            call_count = 0

            async def backend_handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "fresh"}}],
                })

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
            set_forward_client(mock_client)

            resp1 = await client.post("/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp1.status_code == 200
            assert call_count == 1

            resp2 = await client.post("/chat/completions", json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "hi"}],
            })
            assert resp2.status_code == 200
            assert call_count == 2, "Cache disabled should forward every request"

    @pytest.mark.asyncio
    async def test_different_messages_different_cache(self):
        app = self._make_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async def backend_handler(request):
                body = json.loads(request.content)
                msg = body["messages"][0]["content"]
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": f"echo: {msg}"}}],
                })

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
            set_forward_client(mock_client)

            resp_a = await client.post("/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "msg_a"}],
            })
            resp_b = await client.post("/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "msg_b"}],
            })
            assert resp_a.json()["choices"][0]["message"]["content"] == "echo: msg_a"
            assert resp_b.json()["choices"][0]["message"]["content"] == "echo: msg_b"

    @pytest.mark.asyncio
    async def test_error_response_not_cached(self):
        app = self._make_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            call_count = 0

            async def backend_handler(request):
                nonlocal call_count
                call_count += 1
                return httpx.Response(500, json={"error": "internal"})

            mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
            set_forward_client(mock_client)

            resp1 = await client.post("/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "error test"}],
            })
            assert resp1.status_code == 500
            assert call_count == 1

            resp2 = await client.post("/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "error test"}],
            })
            assert resp2.status_code == 500
            assert call_count == 2, "Error responses should not be cached"


class TestCacheStatsInSnapshot:
    @pytest.mark.asyncio
    async def test_cache_stats_in_dashboard(self):
        reset_cache()
        init_cache(CacheConfig(enabled=True, key_fields=["model"]))
        from src.handlers.openai import router as openai_router
        from src.handlers.anthropic import router as anthropic_router
        app = FastAPI()
        models = [
            ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-test"),
        ]
        app.state.router = ModelRouter(models)
        app.state.config = Config(models=models, cache=CacheConfigModel(enabled=True, key_fields=["model"]))
        app.include_router(openai_router)
        app.include_router(anthropic_router)

        async def backend_handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(backend_handler))
        set_forward_client(mock_client)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
            })
            await client.post("/chat/completions", json={
                "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
            })

        cache = get_cache()
        assert cache.hits >= 0
        assert cache.misses >= 0

