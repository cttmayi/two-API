# two-API

A transparent LLM API proxy supporting OpenAI-compatible and Anthropic-compatible APIs, routing requests by model name to different backends.

## Installation

```bash
# From GitHub
pip install git+https://github.com/cttmayi/two-API.git

# Local development
pip install -e ".[dev]"
```

## Configuration

Copy `config.yaml.example` to `config.yaml` (added to `.gitignore`), then edit the model and backend info:

```yaml
server:
  host: "0.0.0.0"
  port: 8080

models:
  # OpenAI official
  - names:
      - gpt-4o
      - gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key

  # Anthropic official
  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-anthropic-key

  # Global aliases: the model field in requests is first looked up in this
  # mapping; if matched, the value replaces the model name before routing
  alias:
    default: gpt-4o-mini
    pro: gpt-4o

logging:
  level: INFO
  output: file
  dir: ./logs
```

- `alias`: Global model aliases (optional). The `model` field in requests is looked up in this mapping first; if matched, the value replaces the model name before normal routing. Useful for centralized alias management without modifying the models config.
- `names`: List of model names that match the `model` field in request body. Two formats are supported:
  - Plain string `gpt-4o`: the client and backend use the same model name
  - Alias mapping `fast: gpt-4o-mini`: the client requests `"fast"` and the proxy forwards as `"gpt-4o-mini"`
- `openai_base_url` / `anthropic_base_url`: At least one must be configured. The proxy appends the request path to this URL.
- `api_key`: Optional. When set, it is injected as `Authorization: Bearer <key>` into backend requests.

## Running

Start via CLI after installation:

```bash
pip install -e .
two-api                    # defaults to ./config.yaml
two-api /path/to/config.yaml
two-api --host 127.0.0.1 --port 9000
```

Or directly with uvicorn:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

## Testing

### OpenAI-compatible Endpoints

**Chat Completions (non-streaming):**

```bash
curl http://0.0.0.0:8080/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {"role": "user", "content": "Hello, who are you?"}
    ]
  }'
```

**Chat Completions (streaming):**

```bash
curl http://0.0.0.0:8080/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [
      {"role": "user", "content": "Tell me a story"}
    ],
    "stream": true
  }'
```

**List Models:**

```bash
curl http://0.0.0.0:8080/models
```

**Embeddings:**

```bash
curl http://0.0.0.0:8080/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "input": "Hello world"
  }'
```

### Anthropic-compatible Endpoints

Both `/messages` and `/v1/messages` are supported (compatible with clients like Claude Code that default to a `/v1` prefix).

**Messages (non-streaming):**

```bash
curl http://0.0.0.0:8080/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 100,
    "messages": [
      {"role": "user", "content": "Hello, who are you?"}
    ]
  }'
```

**Messages (streaming):**

```bash
curl http://0.0.0.0:8080/messages \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "max_tokens": 100,
    "messages": [
      {"role": "user", "content": "Tell me a story"}
    ],
    "stream": true
  }'
```

### Homepage (Status Dashboard)

Visit `/` to see an HTML page showing:

- **Overview cards**: Uptime, total requests, model group count
- **Model configuration table**: Name, backend, API key status
- **Usage statistics** (collapsible): Requests per model, input/output tokens, cache read/write, average latency, average latency per output token
- **Recent requests** (last 50): Time, model, provider, streaming flag, status code, latency, input/output tokens, cache read/write, input/output preview
  - Click a row to expand and view full request/response content and token usage
  - Individual download button per row to save that request as a JSON file

Statistics are recorded for both streaming and non-streaming requests. Cache hits for OpenAI endpoints are extracted from `usage.prompt_tokens_details.cached_tokens`; Anthropic cache reads/writes are extracted from `usage.cache_read_input_tokens` / `usage.cache_creation_input_tokens`.

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Homepage with config, usage stats, and recent requests |
| `/chat/completions` | POST | OpenAI-compatible chat completions |
| `/models` | GET | List available OpenAI-compatible models |
| `/embeddings` | POST | OpenAI-compatible embeddings |
| `/messages`, `/v1/messages` | POST | Anthropic-compatible messages |
| `/recent/download` | GET | Download recent request records (use `?i=N` for a single entry) |

## Running Tests

```bash
python -m pytest -v
```

## Project Structure

```
two-API/
├── config.yaml.example      # Configuration reference
├── pyproject.toml
├── README.md
├── docs/
│   └── README.en.md         # English documentation
├── src/
│   ├── __init__.py
│   ├── main.py              # FastAPI entry point + homepage HTML
│   ├── cli.py               # CLI launcher
│   ├── config.py            # YAML config + Pydantic validation
│   ├── router.py            # Model name → backend matching
│   ├── forwarder.py         # httpx forwarding + streaming
│   ├── stats.py             # Thread-safe stats + recent request tracking
│   ├── logging_setup.py     # structlog configuration
│   └── handlers/
│       ├── __init__.py
│       ├── openai.py        # OpenAI-compatible endpoints
│       └── anthropic.py     # Anthropic-compatible endpoints
└── tests/
    ├── test_config.py
    ├── test_router.py
    ├── test_forwarder.py
    └── test_handlers.py
```
