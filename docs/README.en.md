# two-API

A transparent LLM API proxy supporting OpenAI-compatible and Anthropic-compatible APIs, routing requests by model name to different backends.

## Installation

```bash
# pipx (recommended for macOS/Linux)
pipx install git+https://github.com/cttmayi/two-API.git

# pip (requires a virtual environment)
pip install git+https://github.com/cttmayi/two-API.git

# Local development
pip install -e ".[dev]"
```

## Quick Start

Copy `config.yaml.example` to `~/.two-api/config.yaml` (first time: `mkdir -p ~/.two-api`), then edit the model and backend info:

```yaml
server:
  host: "0.0.0.0"
  port: 8080

models:
  - names:
      - gpt-4o
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key

  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-anthropic-key

logging:
  level: INFO
  output: file
  dir: ~/.two-api/logs
```

Start:

```bash
two-api
```

See [Configuration Guide](config.md) for detailed field descriptions.

## Web Configuration

Visit `http://localhost:8080/settings` after startup to edit configuration in the browser. Supports:

- Add/remove model entries, edit names, backend URLs, API keys, max_tokens, Responses→Chat toggle
- Manage global aliases and cache rules
- Save with hot-reload, no restart required

## Testing

Run all tests:

```bash
python -m pytest -v
```

**Chat Completions:**

```bash
curl http://0.0.0.0:8080/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

**Anthropic Messages:**

```bash
curl http://0.0.0.0:8080/messages \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4-6", "max_tokens": 100, "messages": [{"role": "user", "content": "Hello"}]}'
```

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard with config, hourly usage chart, and recent requests |
| `/chat/completions` | POST | OpenAI-compatible Chat Completions |
| `/responses`, `/v1/responses` | POST | OpenAI-compatible Responses API |
| `/models`, `/v1/models` | GET | List available OpenAI-compatible models |
| `/embeddings` | POST | OpenAI-compatible embeddings |
| `/messages`, `/v1/messages` | POST | Anthropic-compatible messages |
| `/recent/download` | GET | Download recent request records (use `?i=N` for a single entry) |

## Project Structure

```
two-API/
├── config.yaml.example      # Configuration reference
├── pyproject.toml
├── README.md
├── src/
│   ├── main.py              # FastAPI entry point + dashboard HTML
│   ├── cli.py               # CLI launcher
│   ├── config.py            # YAML config + Pydantic validation
│   ├── cache.py             # LLM response cache
│   ├── router.py            # Model name → backend matching
│   ├── forwarder.py         # httpx forwarding + streaming
│   ├── stats.py             # Thread-safe stats + hourly usage persistence + recent requests
│   ├── logging_setup.py     # structlog configuration
│   └── handlers/
│       ├── openai.py        # OpenAI-compatible endpoints
│       └── anthropic.py     # Anthropic-compatible endpoints
└── tests/
    ├── test_config.py
    ├── test_router.py
    ├── test_forwarder.py
    ├── test_handlers.py
    ├── test_dashboard.py
    └── test_stats.py
```
