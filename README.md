# two-API

透明 LLM API 代理，支持 OpenAI 兼容和 Anthropic 兼容 API，按模型名称路由到不同后端。

## 安装

```bash
pip install -e ".[dev]"
```

## 配置

复制 `config.yaml.example` 为 `config.yaml`，编辑模型和后端信息：

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

  # 火山方舟 (OpenAI 兼容)
  - names:
      - glm-5.1
    openai_base_url: https://ark.cn-beijing.volces.com/api/v3
    api_key: ark-your-key

logging:
  level: INFO
  output: file
  dir: ./logs
```

- `names`: 模型名列表，匹配请求 body 中的 `model` 字段
- `openai_base_url` / `anthropic_base_url`: 至少配置一个，代理将请求路径拼接到此 URL
- `api_key`: 可选，配置后将作为 `Authorization: Bearer <key>` 注入到后端请求

## 运行

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

## 端点一览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/chat/completions` | POST | OpenAI 兼容对话接口 |
| `/models` | GET | 列出可用 OpenAI 兼容模型 |
| `/embeddings` | POST | OpenAI 兼容向量接口 |
| `/messages` | POST | Anthropic 兼容对话接口 |

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
