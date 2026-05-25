# two-API

透明 LLM API 代理，支持 OpenAI 兼容和 Anthropic 兼容 API，按模型名称路由到不同后端。

## 安装

```bash
pip install -e ".[dev]"
```

## 配置

复制 `config.yaml.example` 为 `config.yaml`（已加入 `.gitignore`），编辑模型和后端信息：

```yaml
server:
  host: "0.0.0.0"
  port: 8080

models:
  # OpenAI 官方
  - names:
      - gpt-4o
      - gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key

  # Anthropic 官方
  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-anthropic-key

  # 模型别名: 客户端用 "fast" 请求, 代理转发 "gpt-4o-mini"
  - names:
      - fast: gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key

logging:
  level: INFO
  output: file
  dir: ./logs
```

- `names`: 模型名列表，匹配请求 body 中的 `model` 字段。支持两种格式：
  - 普通字符串 `gpt-4o`：客户端和后台用同一个模型名
  - 别名映射 `fast: gpt-4o-mini`：客户端用 `"fast"` 请求，代理转发为 `"gpt-4o-mini"`
- `openai_base_url` / `anthropic_base_url`: 至少配置一个，代理将请求路径拼接到此 URL
- `api_key`: 可选，配置后将作为 `Authorization: Bearer <key>` 注入到后端请求

## 运行

安装后通过 CLI 启动：

```bash
pip install -e .
two-api                    # 默认读取 ./config.yaml
two-api /path/to/config.yaml
two-api --host 127.0.0.1 --port 9000
```

或直接用 uvicorn：

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080
```

## 测试方法

### OpenAI 兼容端点

**Chat Completions (非流式):**

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

**Chat Completions (流式):**

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

**模型列表:**

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

### Anthropic 兼容端点

支持 `/messages` 和 `/v1/messages`（兼容 Claude Code 等默认带 `/v1` 前缀的客户端）。

**Messages (非流式):**

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

**Messages (流式):**

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

**主页（状态面板）:**

```bash
curl http://0.0.0.0:8080/
```

访问 `/` 返回 HTML 页面，展示：
- 运行时间、总请求数、模型组数
- 模型配置表（名称、后端、API Key 状态）
- 使用统计表（请求数、输入/输出 token、缓存命中/写入、平均延迟）

流式和非流式请求均记录统计。OpenAI 端点的缓存命中从 `usage.prompt_tokens_details.cached_tokens` 提取，Anthropic 端点的缓存命中/写入从 `usage.cache_read_input_tokens` / `usage.cache_creation_input_tokens` 提取。

## 端点一览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 主页，展示配置和使用统计 |
| `/chat/completions` | POST | OpenAI 兼容对话接口 |
| `/models` | GET | 列出可用 OpenAI 兼容模型 |
| `/embeddings` | POST | OpenAI 兼容向量接口 |
| `/messages`、`/v1/messages` | POST | Anthropic 兼容对话接口 |

## 运行测试

```bash
python -m pytest -v
```

## 项目结构

```
two-API/
├── config.yaml.example      # 配置参考
├── pyproject.toml
├── src/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # YAML 配置 + Pydantic 校验
│   ├── router.py            # 模型名 → 后端匹配
│   ├── forwarder.py         # httpx 转发 + 流式
│   ├── logging_setup.py     # structlog 配置
│   └── handlers/
│       ├── openai.py        # OpenAI 兼容端点
│       └── anthropic.py     # Anthropic 兼容端点
└── tests/
    ├── test_config.py
    ├── test_router.py
    ├── test_forwarder.py
    └── test_handlers.py
```
