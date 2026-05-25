# LLM API Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a transparent LLM API proxy supporting OpenAI and Anthropic compatible APIs with model-based routing and structured logging.

**Architecture:** FastAPI server loads YAML config at startup, exposes `/v1/` endpoints for both API formats. A model router maps the `model` field from request bodies to configured backends. httpx forwards requests with streaming support. structlog writes JSON-line logs to timestamped files.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, httpx, Pydantic v2, PyYAML, structlog, pytest

---

## File Structure

```
two-API/
├── config.yaml                  # Example config (Task 10)
├── pyproject.toml               # Project metadata + deps (Task 1)
├── src/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, startup, lifespan (Task 8)
│   ├── config.py                # Pydantic models + YAML loader (Task 2)
│   ├── router.py                # ModelRouter class (Task 3)
│   ├── forwarder.py             # httpx forwarding + streaming (Task 5)
│   ├── logging_setup.py         # structlog config (Task 4)
│   └── handlers/
│       ├── __init__.py
│       ├── openai.py            # OpenAI-compatible endpoints (Task 6)
│       └── anthropic.py         # Anthropic-compatible endpoints (Task 7)
└── tests/
    ├── conftest.py              # Shared fixtures (Task 1)
    ├── test_config.py           # Config tests (Task 2)
    ├── test_router.py           # Router tests (Task 3)
    ├── test_forwarder.py        # Forwarder tests (Task 5)
    └── test_handlers.py         # Handler integration tests (Task 9)
```

Each task produces a self-contained, testable unit. Tests are written first (TDD).

---

### Task 1: Project Setup

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/handlers/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "two-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "httpx>=0.28.0",
    "pyyaml>=6.0",
    "pydantic>=2.0",
    "structlog>=24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
]
```

- [ ] **Step 2: Create empty __init__.py files**

```bash
touch src/__init__.py src/handlers/__init__.py tests/__init__.py
```

- [ ] **Step 3: Create conftest.py with base fixtures**

```python
import pytest
import tempfile
import os
from pathlib import Path


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory for config files during tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config_yaml():
    """Return a minimal valid config YAML string."""
    return """
server:
  host: "127.0.0.1"
  port: 8080

models:
  - names:
      - gpt-4o
    openai_base_url: https://api.openai.com
    api_key: sk-test

  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com
    api_key: sk-ant-test

logging:
  level: INFO
  output: file
  dir: ./logs
"""
```

- [ ] **Step 4: Install dependencies**

```bash
pip install -e ".[dev]"
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/__init__.py src/handlers/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore: scaffold project structure and dependencies"
```

---

### Task 2: Config Models and YAML Loading

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests for config**

```python
import pytest
import yaml
from pathlib import Path
from src.config import Config, ModelEntry, ServerConfig, LoggingConfig, load_config


class TestModelEntry:
    def test_valid_openai_only(self):
        entry = ModelEntry(names=["gpt-4o"], openai_base_url="https://api.openai.com", api_key="sk-xxx")
        assert entry.names == ["gpt-4o"]
        assert entry.openai_base_url == "https://api.openai.com"
        assert entry.anthropic_base_url is None

    def test_valid_both_base_urls(self):
        entry = ModelEntry(
            names=["deepseek-chat"],
            openai_base_url="https://api.deepseek.com",
            anthropic_base_url="https://api.deepseek.com/anthropic",
        )
        assert entry.openai_base_url == "https://api.deepseek.com"
        assert entry.anthropic_base_url == "https://api.deepseek.com/anthropic"

    def test_missing_both_base_urls_raises(self):
        with pytest.raises(ValueError, match="At least one of openai_base_url or anthropic_base_url must be set"):
            ModelEntry(names=["bad-model"])

    def test_empty_names_raises(self):
        with pytest.raises(ValueError):
            ModelEntry(names=[], openai_base_url="https://api.openai.com")

    def test_api_key_optional(self):
        entry = ModelEntry(names=["local"], openai_base_url="http://localhost:8000")
        assert entry.api_key is None


class TestServerConfig:
    def test_defaults(self):
        cfg = ServerConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8080


class TestLoggingConfig:
    def test_defaults(self):
        cfg = LoggingConfig()
        assert cfg.level == "INFO"
        assert cfg.output == "file"
        assert cfg.dir == "./logs"


class TestLoadConfig:
    def test_load_valid_config(self, tmp_path):
        data = {
            "server": {"host": "127.0.0.1", "port": 9000},
            "models": [
                {"names": ["gpt-4o"], "openai_base_url": "https://api.openai.com", "api_key": "sk-xxx"},
                {"names": ["claude-sonnet-4-6"], "anthropic_base_url": "https://api.anthropic.com"},
            ],
            "logging": {"level": "DEBUG", "output": "file", "dir": "/var/log"},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.dump(data))

        cfg = load_config(str(path))
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 9000
        assert len(cfg.models) == 2
        assert cfg.models[0].names == ["gpt-4o"]
        assert cfg.models[1].names == ["claude-sonnet-4-6"]
        assert cfg.logging.level == "DEBUG"

    def test_load_config_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/config.yaml")

    def test_load_config_invalid_yaml(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("not: valid: yaml: [")
        with pytest.raises(yaml.YAMLError):
            load_config(str(path))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_config.py -v
```

Expected: FAIL — module `src.config` not found

- [ ] **Step 3: Implement config.py**

```python
from pydantic import BaseModel, model_validator, Field
import yaml


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class LoggingConfig(BaseModel):
    level: str = "INFO"
    output: str = "file"
    dir: str = "./logs"


class ModelEntry(BaseModel):
    names: list[str] = Field(min_length=1)
    openai_base_url: str | None = None
    anthropic_base_url: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def check_at_least_one_base_url(self):
        if not self.openai_base_url and not self.anthropic_base_url:
            raise ValueError("At least one of openai_base_url or anthropic_base_url must be set")
        return self


class Config(BaseModel):
    server: ServerConfig = ServerConfig()
    models: list[ModelEntry]
    logging: LoggingConfig = LoggingConfig()


def load_config(path: str) -> Config:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return Config(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_config.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add config models and YAML loader"
```

---

### Task 3: Model Router

**Files:**
- Create: `src/router.py`
- Create: `tests/test_router.py`

- [ ] **Step 1: Write failing tests for router**

```python
import pytest
from src.config import ModelEntry
from src.router import ModelRouter


class TestModelRouter:
    @pytest.fixture
    def entries(self):
        return [
            ModelEntry(names=["gpt-4o", "gpt-4o-mini"], openai_base_url="https://api.openai.com", api_key="sk-1"),
            ModelEntry(names=["claude-sonnet-4-6"], anthropic_base_url="https://api.anthropic.com", api_key="sk-2"),
            ModelEntry(
                names=["deepseek-chat"],
                openai_base_url="https://api.deepseek.com",
                anthropic_base_url="https://api.deepseek.com/anthropic",
                api_key="sk-3",
            ),
            ModelEntry(names=["local-llama"], openai_base_url="http://localhost:8000"),
        ]

    @pytest.fixture
    def router(self, entries):
        return ModelRouter(entries)

    def test_match_openai_model(self, router):
        entry = router.match("gpt-4o", "openai")
        assert entry is not None
        assert entry.openai_base_url == "https://api.openai.com"

    def test_match_anthropic_model(self, router):
        entry = router.match("claude-sonnet-4-6", "anthropic")
        assert entry is not None
        assert entry.anthropic_base_url == "https://api.anthropic.com"

    def test_match_dual_format_model_openai(self, router):
        entry = router.match("deepseek-chat", "openai")
        assert entry is not None
        assert entry.openai_base_url == "https://api.deepseek.com"

    def test_match_dual_format_model_anthropic(self, router):
        entry = router.match("deepseek-chat", "anthropic")
        assert entry is not None
        assert entry.anthropic_base_url == "https://api.deepseek.com/anthropic"

    def test_match_unknown_model_returns_none(self, router):
        entry = router.match("nonexistent-model", "openai")
        assert entry is None

    def test_match_wrong_endpoint_type_returns_none(self, router):
        """claude-sonnet-4-6 has no openai_base_url, so matching on openai endpoint should fail."""
        entry = router.match("claude-sonnet-4-6", "openai")
        assert entry is None

    def test_match_anthropic_model_on_anthropic_endpoint(self, router):
        """gpt-4o has no anthropic_base_url, so matching on anthropic endpoint should fail."""
        entry = router.match("gpt-4o", "anthropic")
        assert entry is None

    def test_list_openai_models(self, router):
        models = router.list_models("openai")
        model_names = sorted(models)
        assert model_names == sorted(["gpt-4o", "gpt-4o-mini", "deepseek-chat", "local-llama"])

    def test_list_anthropic_models(self, router):
        models = router.list_models("anthropic")
        model_names = sorted(models)
        assert model_names == sorted(["claude-sonnet-4-6", "deepseek-chat"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_router.py -v
```

Expected: FAIL — module `src.router` not found

- [ ] **Step 3: Implement router.py**

```python
from src.config import ModelEntry


class ModelRouter:
    def __init__(self, models: list[ModelEntry]):
        self._models = models

    def match(self, model_name: str, provider: str) -> ModelEntry | None:
        """Find a ModelEntry by model name, checking it supports the given provider endpoint type.

        Args:
            model_name: The model name from the request body.
            provider: 'openai' or 'anthropic' — which endpoint type the request came in on.

        Returns:
            ModelEntry if found and compatible, None otherwise.
        """
        for entry in self._models:
            if model_name in entry.names:
                if provider == "openai" and entry.openai_base_url:
                    return entry
                if provider == "anthropic" and entry.anthropic_base_url:
                    return entry
                return None
        return None

    def list_models(self, provider: str) -> list[str]:
        """List all model names available for a given provider endpoint type."""
        result = []
        for entry in self._models:
            if provider == "openai" and entry.openai_base_url:
                result.extend(entry.names)
            elif provider == "anthropic" and entry.anthropic_base_url:
                result.extend(entry.names)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_router.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/router.py tests/test_router.py
git commit -m "feat: add model router with provider-aware matching"
```

---

### Task 4: Logging Setup

**Files:**
- Create: `src/logging_setup.py`

- [ ] **Step 1: Implement logging_setup.py**

No separate test file needed — logging is tested implicitly through handler tests later.

```python
import logging
import structlog
import os
from datetime import datetime
from pathlib import Path


def setup_logging(log_dir: str, level: str = "INFO") -> str:
    """Configure structlog with dual output: JSON to file, readable to stdout.

    Returns the path of the created log file.
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(log_dir, f"{timestamp}.log")

    level_num = getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer()  # replaced per output below
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Reconfigure with both outputs
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level_num)

    # File handler: JSON
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(level_num)
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer()
    )
    file_handler.setFormatter(file_formatter)

    # Stdout handler: readable
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level_num)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer()
    )
    console_handler.setFormatter(console_formatter)

    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return log_path


def get_logger(name: str | None = None):
    return structlog.get_logger(name or __name__)
```

- [ ] **Step 2: Commit**

```bash
git add src/logging_setup.py
git commit -m "feat: add structlog setup with JSON file and console output"
```

---

### Task 5: Request Forwarder

**Files:**
- Create: `src/forwarder.py`
- Create: `tests/test_forwarder.py`

- [ ] **Step 1: Write unit tests for _prepare_headers**

```python
import pytest
from src.forwarder import _prepare_headers


class TestPrepareHeaders:
    def test_strips_hop_by_hop_headers(self):
        headers = {
            "host": "original.example.com",
            "transfer-encoding": "chunked",
            "connection": "keep-alive",
            "keep-alive": "timeout=5",
            "content-type": "application/json",
            "accept": "application/json",
        }
        result = _prepare_headers(headers, api_key=None)
        assert "host" not in result
        assert "transfer-encoding" not in result
        assert "connection" not in result
        assert "keep-alive" not in result
        assert result["content-type"] == "application/json"
        assert result["accept"] == "application/json"

    def test_injects_authorization_when_api_key_set(self):
        headers = {"content-type": "application/json"}
        result = _prepare_headers(headers, api_key="sk-test")
        assert result["authorization"] == "Bearer sk-test"

    def test_no_authorization_when_api_key_is_none(self):
        headers = {"content-type": "application/json"}
        result = _prepare_headers(headers, api_key=None)
        assert "authorization" not in result

    def test_no_authorization_when_api_key_is_empty_string(self):
        headers = {"content-type": "application/json"}
        result = _prepare_headers(headers, api_key="")
        assert "authorization" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_forwarder.py -v
```

Expected: FAIL — module `src.forwarder` not found

- [ ] **Step 3: Implement forwarder.py**

```python
import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

HOP_BY_HOP_HEADERS = {
    "host",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
}

_forward_client: httpx.AsyncClient | None = None


def get_forward_client() -> httpx.AsyncClient:
    global _forward_client
    if _forward_client is None:
        _forward_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    return _forward_client


def set_forward_client(client: httpx.AsyncClient):
    global _forward_client
    _forward_client = client


def reset_forward_client():
    global _forward_client
    _forward_client = None


def _prepare_headers(headers: dict, api_key: str | None) -> dict:
    result = {}
    for key, value in headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS:
            result[key] = value
    if api_key:
        result["authorization"] = f"Bearer {api_key}"
    return result


def _build_backend_url(base_url: str, path: str, query_string: str) -> str:
    url = base_url.rstrip("/") + path
    if query_string:
        url += "?" + query_string
    return url


async def forward_non_stream(
    request: Request,
    base_url: str,
    api_key: str | None,
    body: bytes | None = None,
) -> Response:
    url = _build_backend_url(base_url, request.url.path, request.url.query)
    headers = _prepare_headers(dict(request.headers), api_key)
    if body is None:
        body = await request.body()

    client = get_forward_client()
    resp = await client.request(
        method=request.method,
        url=url,
        headers=headers,
        content=body,
    )

    response_headers = {}
    for key, value in resp.headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS:
            response_headers[key] = value

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=response_headers,
    )


async def forward_stream(
    request: Request,
    base_url: str,
    api_key: str | None,
    body: bytes | None = None,
) -> StreamingResponse:
    url = _build_backend_url(base_url, request.url.path, request.url.query)
    headers = _prepare_headers(dict(request.headers), api_key)
    if body is None:
        body = await request.body()

    client = get_forward_client()

    async def stream_bytes():
        async with client.stream(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
        ) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk

    return StreamingResponse(stream_bytes(), media_type="text/event-stream")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_forwarder.py -v
```

Expected: `_prepare_headers` tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/forwarder.py tests/test_forwarder.py
git commit -m "feat: add request forwarder with streaming support"
```

---

### Task 6: OpenAI Handler

**Files:**
- Create: `src/handlers/openai.py`

- [ ] **Step 1: Implement OpenAI handler**

```python
import json
import time
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from src.router import ModelRouter
from src.forwarder import forward_non_stream, forward_stream
from src.logging_setup import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _get_router(request: Request) -> ModelRouter:
    return request.app.state.router


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    model_name = body_json.get("model")
    if not model_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'model' field"})

    model_router = _get_router(request)
    entry = model_router.match(model_name, "openai")
    if entry is None:
        if model_router.match(model_name, "anthropic"):
            return JSONResponse(
                status_code=404,
                content={"error": f"Model '{model_name}' not available on this endpoint"},
            )
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    start = time.perf_counter()
    streaming = body_json.get("stream", False)

    try:
        if streaming:
            return await forward_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes)
        else:
            resp = await forward_non_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes)
            latency_ms = int((time.perf_counter() - start) * 1000)

            prompt_tokens = None
            completion_tokens = None
            try:
                resp_body = json.loads(resp.body)
                usage = resp_body.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            logger.info(
                "proxy_request",
                model=model_name,
                provider="openai",
                backend=entry.openai_base_url,
                method=request.method,
                path=request.url.path,
                latency_ms=latency_ms,
                status=resp.status_code,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            return resp
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
    except httpx.TimeoutException:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})


@router.get("/v1/models")
async def list_models(request: Request):
    model_router = _get_router(request)
    models = model_router.list_models("openai")
    return JSONResponse(content={
        "object": "list",
        "data": [{"id": name, "object": "model"} for name in models],
    })


@router.post("/v1/embeddings")
async def embeddings(request: Request):
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    model_name = body_json.get("model")
    if not model_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'model' field"})

    model_router = _get_router(request)
    entry = model_router.match(model_name, "openai")
    if entry is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    resp = await forward_non_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes)
    return resp
```

- [ ] **Step 2: Check for import errors**

```bash
python -c "from src.handlers.openai import router"
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/handlers/openai.py
git commit -m "feat: add OpenAI-compatible handler endpoints"
```

---

### Task 7: Anthropic Handler

**Files:**
- Create: `src/handlers/anthropic.py`

- [ ] **Step 1: Implement Anthropic handler**

```python
import json
import time
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from src.forwarder import forward_non_stream, forward_stream
from src.logging_setup import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _get_router(request: Request):
    return request.app.state.router


@router.post("/v1/messages")
async def messages(request: Request):
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    model_name = body_json.get("model")
    if not model_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'model' field"})

    model_router = _get_router(request)
    entry = model_router.match(model_name, "anthropic")
    if entry is None:
        if model_router.match(model_name, "openai"):
            return JSONResponse(
                status_code=404,
                content={"error": f"Model '{model_name}' not available on this endpoint"},
            )
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    start = time.perf_counter()
    streaming = body_json.get("stream", False)

    try:
        if streaming:
            return await forward_stream(request, entry.anthropic_base_url, entry.api_key, body=body_bytes)
        else:
            resp = await forward_non_stream(request, entry.anthropic_base_url, entry.api_key, body=body_bytes)
            latency_ms = int((time.perf_counter() - start) * 1000)

            input_tokens = None
            output_tokens = None
            try:
                resp_body = json.loads(resp.body)
                usage = resp_body.get("usage", {})
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            logger.info(
                "proxy_request",
                model=model_name,
                provider="anthropic",
                backend=entry.anthropic_base_url,
                method=request.method,
                path=request.url.path,
                latency_ms=latency_ms,
                status=resp.status_code,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            return resp
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
    except httpx.TimeoutException:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
```

- [ ] **Step 2: Check for import errors**

```bash
python -c "from src.handlers.anthropic import router"
```

Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/handlers/anthropic.py
git commit -m "feat: add Anthropic-compatible handler endpoints"
```

---

### Task 8: Main App Entry Point

**Files:**
- Create: `src/main.py`

- [ ] **Step 1: Implement main.py**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.config import load_config
from src.router import ModelRouter
from src.logging_setup import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    config = load_config("config.yaml")
    app.state.config = config
    app.state.router = ModelRouter(config.models)
    log_path = setup_logging(config.logging.dir, config.logging.level)
    app.state.log_path = log_path
    import structlog
    logger = structlog.get_logger()
    logger.info("proxy_startup", log_path=log_path, host=config.server.host, port=config.server.port)
    yield
    # Shutdown
    from src.forwarder import get_forward_client
    client = get_forward_client()
    await client.aclose()


app = FastAPI(lifespan=lifespan)

from src.handlers.openai import router as openai_router
from src.handlers.anthropic import router as anthropic_router

app.include_router(openai_router)
app.include_router(anthropic_router)
```

- [ ] **Step 2: Verify app can be imported**

```bash
python -c "from src.main import app; print('OK')"
```

Expected: fails because config.yaml doesn't exist yet — expected, handled in Task 10

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: add FastAPI app entry point with lifespan"
```

---

### Task 9: Handler Integration Tests

**Files:**
- Create: `tests/test_handlers.py`

- [ ] **Step 1: Write integration tests**

```python
import json
import pytest
import httpx
from fastapi import FastAPI
from src.config import ModelEntry, ServerConfig, LoggingConfig
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
async def client(app_with_models):
    transport = httpx.ASGITransport(app=app_with_models)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


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
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_handlers.py -v
```

Expected: PASS

Fix any failures inline.

- [ ] **Step 3: Commit**

```bash
git add tests/test_handlers.py
git commit -m "test: add handler integration tests for OpenAI and Anthropic endpoints"
```

---

### Task 10: Example Config

**Files:**
- Create: `config.yaml`

- [ ] **Step 1: Create example config.yaml**

```yaml
server:
  host: "0.0.0.0"
  port: 8080

models:
  - names:
      - gpt-4o
      - gpt-4o-mini
    openai_base_url: https://api.openai.com
    api_key: sk-your-openai-key

  - names:
      - claude-sonnet-4-6
      - claude-opus-4-7
    anthropic_base_url: https://api.anthropic.com
    api_key: sk-ant-your-anthropic-key

  - names:
      - deepseek-chat
    openai_base_url: https://api.deepseek.com
    api_key: sk-your-deepseek-key

logging:
  level: INFO
  output: file
  dir: ./logs
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest -v
```

Expected: all PASS

- [ ] **Step 3: Final commit**

```bash
git add config.yaml
git commit -m "chore: add example config.yaml"
```

---

## Running the Proxy

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

Environment variables for API keys should be set in config.yaml directly (as `api_key` fields).