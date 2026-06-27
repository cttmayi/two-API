# 配置文件说明

`two-api` 默认读取 `~/.two-api/config.yaml`。首次使用可先创建目录并复制示例配置：

```bash
mkdir -p ~/.two-api
cp config.yaml.example ~/.two-api/config.yaml
```

也可以启动时指定配置文件路径：

```bash
two-api /path/to/config.yaml
```

## 完整示例

```yaml
server:
  host: "0.0.0.0"
  port: 8080

models:
  - names:
      - gpt-4o
      - fast: gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key
    max_tokens: 4096
    responses_to_chat: false

  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-anthropic-key

alias:
  default: gpt-4o-mini
  pro: gpt-4o

logging:
  level: INFO
  output: file
  dir: ~/.two-api/logs
```

## 顶层字段

| 字段 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `server` | 否 | 见下方 | 服务监听地址和端口 |
| `models` | 是 | 无 | 模型路由配置列表，至少配置一个模型 |
| `alias` | 否 | `{}` | 全局模型别名映射，请求进入路由前先替换 model |
| `logging` | 否 | 见下方 | 日志配置 |

## `server`

| 字段 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `host` | 否 | `0.0.0.0` | 服务监听地址 |
| `port` | 否 | `8080` | 服务监听端口 |

示例：

```yaml
server:
  host: "127.0.0.1"
  port: 9000
```

## `models`

`models` 是一个列表，每一项表示一组模型名及其后端配置。

| 字段 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `names` | 是 | 无 | 客户端可使用的模型名列表，不能为空 |
| `openai_base_url` | 条件必填 | `null` | OpenAI 兼容后端地址 |
| `anthropic_base_url` | 条件必填 | `null` | Anthropic 兼容后端地址 |
| `api_key` | 否 | `null` | 后端 API Key，配置后注入为 `Authorization: Bearer <key>` |
| `max_tokens` | 否 | `null` | 客户端未传 `max_tokens` / `max_output_tokens` 时自动注入的默认值 |
| `responses_to_chat` | 否 | `false` | 客户端调用 Responses API 时，是否转成 Chat Completions 请求发给后端 |

`openai_base_url` 和 `anthropic_base_url` 至少需要配置一个。只配置 `openai_base_url` 的模型只能用于 OpenAI 兼容端点；只配置 `anthropic_base_url` 的模型只能用于 Anthropic 兼容端点；两者都配置时，同一个模型名可用于两类端点。

### `names` 写法

`names` 支持两种写法。

普通字符串：客户端模型名和后端模型名相同。

```yaml
models:
  - names:
      - gpt-4o
      - gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key
```

映射写法：左侧是客户端请求使用的模型名，右侧是实际转发给后端的模型名。

```yaml
models:
  - names:
      - fast: gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key
```

客户端请求：

```json
{"model": "fast"}
```

转发到后端时会变为：

```json
{"model": "gpt-4o-mini"}
```

### 后端 URL 拼接规则

代理会把当前请求路径拼接到配置的 base URL 后面。例如：

```yaml
openai_base_url: https://api.openai.com/v1
```

客户端请求：

```text
POST /chat/completions
```

后端请求：

```text
POST https://api.openai.com/v1/chat/completions
```

如果后端不是 `/v1` 路径，按服务商要求填写完整前缀即可：

```yaml
openai_base_url: https://ark.cn-beijing.volces.com/api/v3
```

对应后端请求会变为：

```text
https://ark.cn-beijing.volces.com/api/v3/chat/completions
```

注意：如果配置里已经包含 `/v1`，同时客户端请求 `/v1/responses`，最终可能拼成 `/v1/v1/responses`。这类兼容端点是否可用取决于后端路径规则。

### `max_tokens`

`max_tokens` 用于给没有传 token 上限的客户端请求补默认值。

- Chat Completions / Anthropic Messages：客户端未传 `max_tokens` 时注入 `max_tokens`
- Responses API：客户端未传 `max_output_tokens` 时注入 `max_output_tokens`
- 客户端已经传值时，以客户端传入值为准
- 未配置 `max_tokens` 时不注入

示例：

```yaml
models:
  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-anthropic-key
    max_tokens: 8192
```

### `responses_to_chat`

`responses_to_chat` 用于兼容只支持 Chat Completions、但客户端调用 Responses API 的后端。

```yaml
models:
  - names:
      - ark-deepseek-v4-flash
    openai_base_url: https://ark.cn-beijing.volces.com/api/v3
    api_key: ark-your-key
    responses_to_chat: true
```

启用后：

- 客户端请求 `/responses` 或 `/v1/responses`
- 代理把请求转换为后端 `/chat/completions`
- `input` 转换为 `messages`
- `developer` role 会转换为 Chat 兼容的 `system` role
- `input_text` 内容块会展开为纯文本 content，空白消息会过滤
- `instructions` 转换为第一条 `system` message
- `max_output_tokens` 转换为 `max_tokens`
- `temperature`、`top_p`、`stream` 会透传
- function `tools` 会转换为 Chat Completions 的 function tools 格式，非 function tools 会过滤
- 后端 Chat Completions 输出会转换回 Responses API 格式返回给客户端，包括 function tool calls

暂不支持 `previous_response_id`，请求中包含该字段时会返回 `400`。

## `alias`

`alias` 是全局模型别名。请求进入模型路由前，会先检查 body 中的 `model` 是否命中 `alias`，命中后替换为目标模型名。

```yaml
alias:
  default: gpt-4o-mini
  pro: gpt-4o
```

客户端请求：

```json
{"model": "default"}
```

路由前会先替换为：

```json
{"model": "gpt-4o-mini"}
```

`alias` 和 `names` 映射的区别：

- `alias` 是全局替换，适合集中管理常用模型别名
- `names` 映射绑定在某个 model entry 上，适合定义该后端支持的客户端名到后端名映射

## `logging`

| 字段 | 必填 | 默认值 | 说明 |
|---|---:|---|---|
| `level` | 否 | `INFO` | 日志级别，例如 `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `output` | 否 | `file` | 当前配置字段保留，日志会写入文件并输出到控制台 |
| `dir` | 否 | `~/.two-api/logs` | 日志目录 |

示例：

```yaml
logging:
  level: INFO
  output: file
  dir: ~/.two-api/logs
```

日志文件位于 `logging.dir` 下。小时 token 用量不放在 logs 目录内，而是写入 logs 目录的同级路径：

```text
~/.two-api/usage.json
```

## `cache`

| 字段 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `enabled` | 否 | `false` | 总开关 |
| `ttl_seconds` | 否 | `3600` | 缓存过期时间（秒），`0` 表示永不过期 |
| `max_entries` | 否 | `2000` | 最大缓存条目数，超过后 LRU 淘汰 |
| `aliases` | 否 | `[]` | 允许缓存的 alias 列表。空列表 = 放行所有 alias |
| `key_fields` | 否 | `[]` | 额外参与 cache key 的请求 body 字段。`messages` 始终参与。当 `alias` 在列表中时，只有带 alias 的请求才缓存 |

示例：

```yaml
cache:
  enabled: true
  ttl_seconds: 3600
  max_entries: 2000
  aliases:
    - default
  key_fields:
    - model
    - alias
```

### 缓存机制

对相同请求（由 `messages` + `key_fields` 配置的字段共同决定）直接返回缓存结果，降低延迟和 API 费用。

- 缓存命中时跳过后端转发，直接返回缓存内容（非流式/流式回放）
- 命中率在 dashboard 顶部显示（Cache Hits / Cache Misses）
- 默认使用内存 LRU + TTL 淘汰策略
- 错误响应（非 200）不会被缓存

流式和非流式请求均支持缓存。流式请求缓存的是 SSE event 列表，命中时按原始顺序回放。

## 常见配置

### OpenAI 官方

```yaml
models:
  - names:
      - gpt-4o
      - gpt-4o-mini
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key
```

### Anthropic 官方

```yaml
models:
  - names:
      - claude-sonnet-4-6
      - claude-opus-4-7
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-anthropic-key
    max_tokens: 8192
```

### 同一个模型名同时支持 OpenAI 和 Anthropic 端点

```yaml
models:
  - names:
      - my-model
    openai_base_url: https://example.com/openai/v1
    anthropic_base_url: https://example.com/anthropic/v1
    api_key: your-key
```

### OpenAI 兼容服务商

```yaml
models:
  - names:
      - deepseek-chat
    openai_base_url: https://api.deepseek.com/v1
    api_key: sk-your-deepseek-key
```

```yaml
models:
  - names:
      - glm-5.1
    openai_base_url: https://ark.cn-beijing.volces.com/api/v3
    api_key: ark-your-key
```

## 测试方法

### OpenAI 兼容端点

**Chat Completions (非流式):**

```bash
curl http://0.0.0.0:8080/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello, who are you?"}]}'
```

**Chat Completions (流式):**

```bash
curl http://0.0.0.0:8080/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Tell me a story"}], "stream": true}'
```

**Responses API:**

```bash
curl http://0.0.0.0:8080/responses \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "input": "Hello, who are you?"}'
```

**模型列表:**

```bash
curl http://0.0.0.0:8080/models
```

**Embeddings:**

```bash
curl http://0.0.0.0:8080/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "input": "Hello world"}'
```

### Anthropic 兼容端点

支持 `/messages` 和 `/v1/messages`（兼容 Claude Code 等默认带 `/v1` 前缀的客户端）。

**Messages (非流式):**

```bash
curl http://0.0.0.0:8080/messages \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4-6", "max_tokens": 100, "messages": [{"role": "user", "content": "Hello, who are you?"}]}'
```

**Messages (流式):**

```bash
curl http://0.0.0.0:8080/messages \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4-6", "max_tokens": 100, "messages": [{"role": "user", "content": "Tell me a story"}], "stream": true}'
```

### Dashboard

访问 `/` 可查看 HTML 状态面板，展示：

- **概览卡片**：运行时间、总请求数、模型组数、缓存命中/未命中
- **模型配置表**：名称、后端、API Key 状态
- **Hourly Token Usage**：最近 24 个小时的 token 柱状图，可按模型或全局 alias 分组堆叠显示；无请求的小时会保留为空柱；悬停可查看请求数、总 token、平均延迟、每输出 token 延迟以及当前分组明细
- **最近请求**（最近 50 条）：时间、alias、模型、提供商、流式标记、状态码、延迟、输入/输出 token、缓存读写、输入/输出预览
  - 点击行展开详情，查看完整请求/响应内容和 token 用量
  - 每行单独下载按钮，保存该次请求为 JSON 文件

流式和非流式请求均记录统计。OpenAI 端点的缓存命中从 `usage.prompt_tokens_details.cached_tokens` 提取，Anthropic 端点的缓存命中/写入从 `usage.cache_read_input_tokens` / `usage.cache_creation_input_tokens` 提取。小时用量会持久化到 `~/.two-api/usage.json`，重启后继续加载；Web 页面最多展示最近 24 个小时。

## 配置检查

可以用下面命令检查配置是否能被加载和解析：

```bash
python -c "from src.config import load_config; c=load_config('~/.two-api/config.yaml'); print(c.model_dump_json(indent=2))"
```
