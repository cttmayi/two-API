# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在本仓库中工作时提供项目约定和维护指引。

## 常用命令

- **运行全部测试：** `python -m pytest -v`
- **运行单个测试：** `python -m pytest tests/test_handlers.py -v -k "test_chat_completions_non_stream"`
- **开发安装：** `pip install -e ".[dev]"`
- **运行代理：** `two-api`（默认读取 `~/.two-api/config.yaml`）或 `uvicorn src.main:app`
- **检查配置：** `python -c "from src.config import load_config; c=load_config('~/.two-api/config.yaml'); print(c.model_dump_json(indent=2))"`

## 架构

该代理位于客户端（例如 Claude Code、openai-python）和 LLM 后端（OpenAI、Anthropic、DeepSeek、Volcengine 等）之间。它接收 API 兼容请求，必要时重写模型名，并转发到对应后端。

### 请求生命周期

```
Client → FastAPI → handler (openai.py 或 anthropic.py) → ModelRouter.match() → forwarder.py (httpx) → Backend LLM API
                                                                  ↓
                                                            stats.py（统计计数 + 最近请求环形缓冲）
```

### 核心模块

- **`src/config.py`** — 通过 Pydantic 加载 YAML 配置。`ModelEntry` 支持每模型 `api_key`、双 `openai_base_url` / `anthropic_base_url`、可选 `max_tokens`、`responses_to_chat`，以及通过 `names` 列表配置模型名别名（字符串或 `{client_name: backend_name}` 字典）。全局 `alias` 会在路由前重写模型名。

- **`src/router.py`** — `ModelRouter` 将 `(model_name, provider)` 匹配到 `ModelEntry` 和后端模型名。只有配置了对应 `*_base_url` 的模型条目才会匹配对应 provider。

- **`src/forwarder.py`** — 基于 httpx 的 HTTP 转发层，负责移除 hop-by-hop header、按需注入 API Key。模块级单例 client 由 `get_forward_client()` 管理，同时支持流式和非流式转发。

- **`src/handlers/openai.py`** — OpenAI 兼容端点：`POST /chat/completions`、`POST /responses`、`POST /v1/responses`、`POST /embeddings`、`GET /models`、`GET /v1/models`。先应用全局 alias，再执行 `ModelRouter.match(model, "openai")`。后端模型名使用 `ModelRouter.match()` 的结果覆盖。流式和非流式路径都会记录统计和最近请求详情。

- **`src/handlers/anthropic.py`** — Anthropic 兼容端点：`POST /messages`、`POST /v1/messages`。整体模式与 OpenAI handler 相同，但使用 `ModelRouter.match(model, "anthropic")`。Anthropic 流式响应会额外累积 content block 供统计使用。

- **`src/main.py`** — FastAPI 应用入口，lifespan 启动时加载配置、设置日志、初始化 router。`GET /` 渲染 HTML dashboard，展示配置表、Hourly Token Usage 图表，以及可展开 JSON 详情和下载的最近请求表。

- **`src/stats.py`** — 线程安全统计模块，包含按模型计数、持久化到 `usage.json` 的按小时 token 用量，以及 `deque(maxlen=50)` 最近请求环形缓冲。`Stats.record_detail()` 会保存截断后的请求/响应内容供 dashboard 展示。

- **`src/transforms/`** — 协议转换层。不同 provider 的请求、响应和流式事件应先转换到共享 IR dataclass，再转换为目标协议格式；不要在 handler 中直接写协议到协议的转换细节。

- **`src/logging_setup.py`** — structlog 日志配置，同时支持 JSON 文件输出和 stdout 可读 ConsoleRenderer 输出。

### 模型路由逻辑

```yaml
models:
  - names: ["gpt-4o", {"fast": "gpt-4o-mini"}]    # 客户端可见模型名
    openai_base_url: https://api.openai.com/v1       # OpenAI 路由需要
    anthropic_base_url: ...                         # Anthropic 路由需要
    api_key: sk-xxx                                  # 每模型可选
```

模型条目必须至少配置 `openai_base_url` 或 `anthropic_base_url` 之一。如果客户端把 `gpt-4o` 发到 `/messages`，但匹配到的条目没有 `anthropic_base_url`，handler 返回 404：`not available on this endpoint`。如果完全没有模型匹配，则返回 404：`Unknown model`。

### 测试模式

测试使用 `httpx.ASGITransport` 在进程内调用 FastAPI，并用 `httpx.MockTransport` 模拟后端响应，不需要访问外部服务。`set_forward_client()` / `reset_forward_client()` 用于替换转发层的 mock httpx client。测试中通常直接构造 `Config` 和 `ModelRouter` fixture，不依赖真实配置文件。

## 维护规则

- 实现或调试过程中不要每次代码变化都立即更新 README / docs；先专注修复和验证。
- 当用户确认改动已成功，或用户要求提交 / 准备提交代码时，再检查是否需要同步更新 `README.md`、`docs/README.en.md`、`docs/config.md`，以及适用时的 `config.yaml.example`。
- 需要在确认成功或提交前检查文档的情况包括：用户可见配置字段、HTTP 端点、请求/响应格式、流式行为、兼容开关、dashboard 行为、统计字段、日志位置、持久化文件、可见 UI 文案。
- 新增核心模块、改变请求路由逻辑、或在模块之间迁移职责时，需要同步更新本文件的架构说明。
- 协议转换应放在 `src/transforms/`，并通过 IR dataclass 进行转换；handler 只负责路由、转发和统计编排，不应持有协议转换细节。
- 除非用户明确要求，否则不要提交 commit。
