# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- **Run all tests:** `python -m pytest -v`
- **Run single test:** `python -m pytest tests/test_handlers.py -v -k "test_chat_completions_non_stream"`
- **Dev install:** `pip install -e ".[dev]"`
- **Run proxy:** `two-api` (reads `~/.two-api/config.yaml`) or `uvicorn src.main:app`
- **Check config:** `python -c "from src.config import load_config; c=load_config('~/.two-api/config.yaml'); print(c.model_dump_json(indent=2))"`

## Architecture

The proxy sits between clients (e.g., Claude Code, openai-python) and LLM backends (OpenAI, Anthropic, DeepSeek, Volcengine). It receives API-compatible requests, rewrites the model name if needed, and forwards to the appropriate backend.

### Request lifecycle

```
Client → FastAPI → handler (openai.py or anthropic.py) → ModelRouter.match() → forwarder.py (httpx) → Backend LLM API
                                                          ↓
                                                    stats.py (in-memory counters + recent requests ring buffer)
```

### Key modules

- **`src/config.py`** — YAML config loading via Pydantic. `ModelEntry` supports per-model `api_key`, dual `openai_base_url`/`anthropic_base_url`, and model name aliasing via `names` list (strings or `{client_name: backend_name}` dicts). Global `alias` dict rewrites model before routing.

- **`src/router.py`** — `ModelRouter` matches a `(model_name, provider)` pair to a `ModelEntry` + backend model name. A model entry only matches for a given provider if the corresponding `*_base_url` is configured.

- **`src/forwarder.py`** — httpx-based HTTP forwarding with hop-by-hop header stripping, optional API key injection. Module-level singleton client (`get_forward_client()`). Both streaming and non-streaming forward functions.

- **`src/handlers/openai.py`** — Routes for `POST /chat/completions`, `POST /embeddings`, `GET /models`, `GET /v1/models`. Global alias applied, then `ModelRouter.match(model, "openai")`. Backend model name override in request body via the `get_name_map()` mapping. Streaming and non-streaming paths both record stats + recent detail.

- **`src/handlers/anthropic.py`** — Routes for `POST /messages`, `POST /v1/messages`. Same pattern as openai handler but with `ModelRouter.match(model, "anthropic")`. Anthropic-specific SSE content block accumulation for stats.

- **`src/main.py`** — FastAPI app with lifespan startup (loads config, sets up logging, initializes router). `GET /` renders HTML dashboard with config table, per-model stats cards, and recent requests table with expandable JSON detail rows and per-request download.

- **`src/stats.py`** — Thread-safe in-memory stats. `Stats` class with per-model counters (requests, tokens by type, total latency) and a `deque(maxlen=50)` ring buffer of recent requests. `Stats.record_detail()` stores truncated request/response content for the dashboard.

- **`src/logging_setup.py`** — structlog configured with dual output: JSON to file, readable ConsoleRenderer to stdout.

### Model routing logic

```
models:
  - names: ["gpt-4o", {"fast": "gpt-4o-mini"}]    # client-facing names
    openai_base_url: https://api.openai.com/v1       # required for OpenAI routing
    anthropic_base_url: ...                           # required for Anthropic routing
    api_key: sk-xxx                                   # per-model, optional
```

A model entry must have at least one of `openai_base_url`/`anthropic_base_url`. If a client sends a request for `gpt-4o` to `/messages` and the matched entry has no `anthropic_base_url`, the handler returns 404 with "not available on this endpoint". If no entry matches at all, it returns 404 with "Unknown model".

### Testing patterns

Tests use `httpx.ASGITransport` for in-process FastAPI calls and `httpx.MockTransport` to simulate backend responses — no external services needed. The `set_forward_client()` / `reset_forward_client()` helpers swap in mock httpx clients for the forwarding layer. `Config` and `ModelRouter` are constructed directly in fixtures (no config file needed).