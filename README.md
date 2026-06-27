# two-API

透明 LLM API 代理，支持 OpenAI 兼容和 Anthropic 兼容 API，按模型名称路由到不同后端。

[English Documentation](docs/README.en.md)

## 安装

```bash
# pipx 安装（推荐，macOS/Linux 通用）
pipx install git+https://github.com/cttmayi/two-API.git

# pip 安装（需在虚拟环境中）
pip install git+https://github.com/cttmayi/two-API.git

# 本地开发安装
pip install -e ".[dev]"
```

## 配置

详细字段说明见：[配置文件说明](docs/config.md)。

复制 `config.yaml.example` 为 `~/.two-api/config.yaml`（首次使用需 `mkdir -p ~/.two-api`），编辑模型和后端信息：

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
    # max_tokens: 8192       # 可选，客户端未传时代理自动注入

  # 全局别名: 请求中 model 字段先查此映射, 匹配则替换后再路由
  alias:
    default: gpt-4o-mini
    pro: gpt-4o

  # 响应缓存（可选，默认启用）
  cache:
    enabled: true
    ttl_seconds: 3600
    max_entries: 2000
    aliases:
      - default
    key_fields:
      - model
      - alias

logging:
  level: INFO
  output: file
  dir: ~/.two-api/logs
```

- `alias`: 全局模型别名（可选），请求中 `model` 字段会先在此映射中查找，匹配则替换后走正常路由。用于集中管理别名，不改 models 配置就能切换
- `names`: 模型名列表，匹配请求 body 中的 `model` 字段。支持两种格式：
  - 普通字符串 `gpt-4o`：客户端和后台用同一个模型名
  - 别名映射 `fast: gpt-4o-mini`：客户端用 `"fast"` 请求，代理转发为 `"gpt-4o-mini"`
- `openai_base_url` / `anthropic_base_url`: 至少配置一个，代理将请求路径拼接到此 URL
- `api_key`: 可选，配置后将作为 `Authorization: Bearer <key>` 注入到后端请求
- `max_tokens`: 可选，客户端请求未传 `max_tokens` 时代理自动注入此值。不配置则不注入，不影响远端有传的情况
- `responses_to_chat`: 可选，设为 `true` 后客户端调用 Responses API 时会转成后端 Chat Completions 请求，再把 Chat 输出转换回 Responses 格式；会将 `developer` role、`input_text` 内容块和 function `tools` 转为 Chat 兼容格式，过滤空白消息，并把 Chat tool calls 转回 Responses function calls
- `cache`: 响应缓存（可选），对相同请求直接返回缓存结果
  - `enabled`: 总开关，默认 `true`
  - `ttl_seconds`: 缓存过期时间，默认 `3600`（1 小时），`0` 表示永不过期
  - `max_entries`: 最大缓存条目数，默认 `2000`，超过后自动淘汰最久未访问条目
  - `aliases`: 允许缓存的 alias 列表。空列表 = 放行所有 alias
  - `key_fields`: 参与 cache key 的请求 body 字段。`messages` 始终参与，此列表额外控制。当 `alias` 在列表中时，只有带 alias 的请求才缓存；不在列表中则所有请求都可缓存

## 缓存机制

对相同请求（由 `messages` + `key_fields` 配置的字段共同决定）直接返回缓存结果，降低延迟和 API 费用。

- 缓存命中时跳过后端转发，直接返回缓存内容（非流式/流式回放）
- 命中率在 dashboard 顶部显示（Cache Hits / Cache Misses）
- 默认使用内存 LRU + TTL 淘汰策略
- 错误响应（非 200）不会被缓存

## 运行

安装后通过 CLI 启动：

```bash
two-api                    # 默认读取 ~/.two-api/config.yaml
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

**Responses API:**

```bash
curl http://0.0.0.0:8080/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "input": "Hello, who are you?"
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

### 主页（状态面板）

访问 `/` 返回 HTML 页面，展示：

- **概览卡片**：运行时间、总请求数、模型组数、缓存命中/未命中
- **模型配置表**：名称、后端、API Key 状态
- **Hourly Token Usage**：最近 24 个小时的 token 柱状图，可按模型或全局 alias 分组堆叠显示；无请求的小时会保留为空柱；悬停可查看请求数、总 token、平均延迟、每输出 token 延迟以及当前分组明细
- **最近请求**（最近 50 条）：时间、alias、模型、提供商、流式标记、状态码、延迟、输入/输出 token、缓存读写、输入/输出预览
  - 点击行展开详情，查看完整请求/响应内容和 token 用量
  - 每行单独下载按钮，保存该次请求为 JSON 文件

流式和非流式请求均记录统计。OpenAI 端点的缓存命中从 `usage.prompt_tokens_details.cached_tokens` 提取，Anthropic 端点的缓存命中/写入从 `usage.cache_read_input_tokens` / `usage.cache_creation_input_tokens` 提取。小时用量会持久化到 `~/.two-api/usage.json`，重启后继续加载；Web 页面最多展示最近 24 个小时。

## 端点一览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 主页，展示配置、小时用量图和最近请求 |
| `/chat/completions` | POST | OpenAI 兼容 Chat Completions 接口 |
| `/responses`、`/v1/responses` | POST | OpenAI 兼容 Responses API |
| `/models`、`/v1/models` | GET | 列出可用 OpenAI 兼容模型 |
| `/embeddings` | POST | OpenAI 兼容向量接口 |
| `/messages`、`/v1/messages` | POST | Anthropic 兼容对话接口 |
| `/recent/download` | GET | 下载最近请求记录（支持 `?i=N` 下载单条） |

## 运行测试

```bash
python -m pytest -v
```

## 项目结构

```
two-API/
├── config.yaml.example      # 配置参考
├── pyproject.toml
├── README.md
├── docs/
│   └── README.en.md         # 英文文档
├── src/
│   ├── __init__.py
│   ├── main.py              # FastAPI 入口 + 主页 HTML
│   ├── cli.py               # CLI 启动入口
│   ├── config.py            # YAML 配置 + Pydantic 校验
│   ├── cache.py             # LLM 响应缓存（TTLCache + cache key）
│   ├── router.py            # 模型名 → 后端匹配
│   ├── forwarder.py         # httpx 转发 + 流式
│   ├── stats.py             # 线程安全统计 + 小时用量持久化 + 最近请求记录
│   ├── logging_setup.py     # structlog 配置
│   └── handlers/
│       ├── __init__.py
│       ├── openai.py        # OpenAI 兼容端点
│       └── anthropic.py     # Anthropic 兼容端点
└── tests/
    ├── test_config.py
    ├── test_router.py
    ├── test_forwarder.py
    ├── test_handlers.py
    ├── test_dashboard.py
    └── test_stats.py
```

