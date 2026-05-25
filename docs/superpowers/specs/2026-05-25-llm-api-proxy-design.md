# LLM API Proxy - Design Spec

## Overview

A transparent LLM API proxy that accepts OpenAI-compatible and Anthropic-compatible API requests and forwards them to configured backends. The proxy routes requests by model name and provides structured logging with latency and token usage tracking.

## Requirements

- **Dual API support**: OpenAI-compatible endpoints (`/chat/completions`, `/models`, `/embeddings`) and Anthropic-compatible endpoints (`/messages`)
- **Transparent proxy**: Requests and responses keep their original format; no format translation
- **Model-based routing**: The `model` field in the request body determines the backend
- **Mixed backends**: Public cloud APIs and self-hosted instances, configured per model
- **Streaming**: SSE streaming responses are forwarded chunk-by-chunk without buffering
- **Logging**: Structured JSON-lines logs written to timestamped files on startup
- **No client auth**: Internal network use, no API key required from clients

## Architecture

```
Client ──▶ FastAPI Server ──▶ ModelRouter ──▶ httpx Forwarder ──▶ Backend
               │                  │
               ▼                  ▼
          Config (YAML)     Logger (structlog)
```

### Project Structure

```
two-API/
├── config.yaml
├── pyproject.toml
├── src/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── config.py            # YAML loading + Pydantic models
│   ├── router.py            # Model name → backend mapping
│   ├── forwarder.py         # httpx request forwarding + streaming
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── openai.py        # OpenAI-compatible endpoints
│   │   └── anthropic.py     # Anthropic-compatible endpoints
│   └── logging_setup.py     # structlog configuration
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_router.py
    ├── test_handlers.py
    └── test_forwarder.py
```

### Dependencies

`fastapi`, `uvicorn`, `httpx`, `pyyaml`, `pydantic`, `structlog`

## Configuration

### config.yaml Schema

```yaml
server:
  host: "0.0.0.0"
  port: 8080

models:
  - names:
      - gpt-4o
      - gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-xxx

  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-xxx

  - names:
      - deepseek-chat
    openai_base_url: https://api.deepseek.com/v1
    api_key: sk-xxx

  - names:
      - glm-5.1
    openai_base_url: https://ark.cn-beijing.volces.com/api/v3
    api_key: ark-xxx

  - names:
      - local-llama
    openai_base_url: http://localhost:8080/v1

logging:
  level: INFO
  output: file
  dir: ./logs
```

### Field Rules

- `names`: Array of model names sharing the same backend. At least one required.
- `openai_base_url` / `anthropic_base_url`: At least one must be configured per entry.
- `api_key`: Optional. If omitted, forwarded requests carry no `Authorization` header.
- `logging.output`: `file` — writes JSON lines to a timestamped file (e.g., `./logs/2026-05-25_14-30-00.log`). Stdout always receives human-readable output for development.
- Config is loaded at startup and validated with Pydantic. Validation failures cause the process to exit with a clear error message.

## Routing

### Model Matching

1. Extract `model` from the request body JSON
2. Find the `ModelEntry` whose `names` list contains that model value
3. If not found, return **404** `{"error": "Unknown model: xxx"}`
4. If the request is on an OpenAI endpoint but the matched entry has no `openai_base_url`, return **404** `{"error": "Model 'xxx' not available on this endpoint"}`
5. Likewise for Anthropic endpoints and `anthropic_base_url`

### URL Construction

The proxy's request path is appended to the matched `base_url`. Example:

```
Proxy receives:  POST /chat/completions  (model: glm-5.1)
Matched base_url: https://ark.cn-beijing.volces.com/api/v3
Backend request:  POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
```

For OpenAI official API, configure `openai_base_url: https://api.openai.com/v1` to get:
```
Proxy receives:  POST /chat/completions
Backend request:  POST https://api.openai.com/v1/chat/completions
```

### Endpoint Exposure

- OpenAI endpoints: `/chat/completions`, `/models`, `/embeddings`
- Anthropic endpoints: `/messages`, `/v1/messages`
- `GET /` serves an HTML homepage showing config summary and per-model usage statistics
- `/models` on the OpenAI side only lists models with `openai_base_url` configured

## Request Forwarding

### Header Handling

- **Pass through**: `Content-Type`, `Accept`, and most client headers
- **Strip**: `Host`, `Transfer-Encoding`, `Connection`, `Content-Length`, `Content-Encoding`, and other hop-by-hop headers. `Content-Length` and `Content-Encoding` are stripped from responses because httpx auto-decodes compressed bodies, making the original values invalid.
- **Inject**: `Authorization: Bearer <api_key>` if `api_key` is configured on the matched entry

### Streaming

- httpx `stream()` with `aiter_bytes()` — each chunk yielded directly to the client, SSE lines accumulated for usage extraction
- After stream ends, usage (including cache tokens) is extracted from SSE `data:` lines and recorded
- Timeout: 300 seconds to cover long conversations and reasoning models

## Error Handling

| Scenario | Status | Response |
|---|---|---|
| Request body missing `model` | 400 | `{"error": "Missing 'model' field"}` |
| Model not found in config | 404 | `{"error": "Unknown model: xxx"}` |
| Model not available on endpoint type | 404 | `{"error": "Model 'xxx' not available on this endpoint"}` |
| Backend connection timeout / unreachable | 502 | `{"error": "Backend unreachable"}` |
| Backend returns error (4xx/5xx) | passthrough | Backend status and body returned unchanged |
| Stream interrupted mid-response | terminate stream | Error logged |

**Principle**: Proxy's own errors return clear JSON. Backend errors are passed through unmodified so clients see the same errors they would from a direct call.

## Logging

Each request logs one JSON line containing:

```json
{
  "event": "proxy_request",
  "model": "deepseek-chat",
  "provider": "openai",
  "backend": "https://api.deepseek.com",
  "method": "POST",
  "path": "/chat/completions",
  "latency_ms": 1234,
  "status": 200,
  "prompt_tokens": 150,
  "completion_tokens": 80,
  "cache_read_tokens": 3120,
  "cache_write_tokens": 0
}
```

- **Non-streaming**: Logged once after response completes. Token counts parsed from response body.
- **Streaming**: Latency, status, and token usage logged when stream ends. Usage merged from multiple SSE events (e.g., Anthropic's `message_start` provides input tokens and cache, `message_delta` provides output tokens).
- **Cache tokens**: OpenAI extracts `cached_tokens` from `prompt_tokens_details`; Anthropic extracts `cache_read_input_tokens` and `cache_creation_input_tokens` from usage.
- **Stats**: All requests (streaming and non-streaming) recorded in-memory via `Stats`, viewable on the `GET /` homepage.
- **Startup**: Creates a new log file named `logs/<YYYY-MM-DD_HH-MM-SS>.log` at process start.
- **Dual output**: JSON lines to file, human-readable to stdout.

## Testing

- **Framework**: pytest with `httpx.AsyncClient` against the FastAPI app (no real server)
- **Mock backends**: `pytest-httpx` or `httpx.MockTransport` to simulate backend responses
- **Key test cases**:
  - Config: valid loading, empty names rejected, missing both base_urls rejected
  - Router: exact match, no match, endpoint-type mismatch
  - Handlers: non-streaming returns correct body, streaming yields chunks, Authorization header injected
  - Forwarder: backend 4xx/5xx passthrough, unreachable backend → 502, mid-stream disconnect