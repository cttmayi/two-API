# OpenAI 兼容格式

本文说明 two-API 对 OpenAI 兼容接口的请求、响应和流式事件格式处理。

two-API 当前支持：

- `POST /chat/completions`
- `POST /responses`
- `POST /v1/responses`
- `GET /models`
- `GET /v1/models`
- `POST /embeddings`

## 通用代理行为

### 路由字段

所有 OpenAI 兼容 POST 接口都依赖请求体中的 `model` 字段路由：

```json
{
  "model": "gpt-4o"
}
```

处理顺序：

1. 读取 JSON 请求体。
2. 检查 `model` 是否存在。
3. 如果配置了全局 `alias`，先把客户端传入的模型名替换成真实模型名。
4. 在配置的 OpenAI 模型组中查找该模型。
5. 如果模型配置里使用了 `names` 映射，例如 `{ "fast": "gpt-4o-mini" }`，转发给后端前会把 body 中的 `model` 改成后端模型名。
6. 原样转发请求到 `openai_base_url + 当前请求路径`。

### 路径拼接细节

后端 URL 的构造方式是：

```text
openai_base_url.rstrip("/") + 当前请求路径
```

因此 `openai_base_url` 是否已经包含 `/v1` 会影响应该调用哪个本地端点：

| 配置 | 本地请求 | 实际后端路径 |
|---|---|---|
| `openai_base_url: https://api.example.com/v1` | `/chat/completions` | `/v1/chat/completions` |
| `openai_base_url: https://api.example.com/v1` | `/responses` | `/v1/responses` |
| `openai_base_url: https://api.example.com/v1` | `/v1/responses` | `/v1/v1/responses` |
| `openai_base_url: https://api.example.com` | `/v1/responses` | `/v1/responses` |

如果 base URL 已经带 `/v1`，通常应调用 two-API 的无 `/v1` 路径，例如 `/responses`；如果 base URL 不带 `/v1`，才适合调用 `/v1/responses`。

### 错误格式

请求体不是合法 JSON：

```json
{
  "error": "Invalid JSON body"
}
```

缺少 `model`：

```json
{
  "error": "Missing 'model' field"
}
```

模型不存在：

```json
{
  "error": "Unknown model: gpt-unknown"
}
```

模型存在但只配置了 Anthropic 后端：

```json
{
  "error": "Model 'claude-sonnet-4-6' not available on this endpoint"
}
```

后端连接失败或超时：

```json
{
  "error": "Backend unreachable"
}
```

后端返回非 200 时，two-API 不改写后端响应 body，会原样返回给客户端；同时会把完整响应文本保存到最近请求记录里用于调试。

## Chat Completions

### 请求

端点：

```http
POST /chat/completions
```

典型请求：

```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7,
  "max_tokens": 1024,
  "stream": false
}
```

关键字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string | 必填，用于匹配模型配置 |
| `messages` | array | 对话消息列表，原样转发给后端 |
| `stream` | boolean | 可选，`true` 时走 SSE 流式转发 |
| `max_tokens` | integer | 可选；如果客户端未传且模型配置了 `max_tokens`，代理会自动注入 |

除 `model` 和 `max_tokens` 注入外，其他字段由代理原样转发。

### 非流式响应

后端响应会原样返回。典型格式：

```json
{
  "id": "chatcmpl_xxx",
  "object": "chat.completion",
  "created": 1710000000,
  "model": "gpt-4o",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello!"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 3,
    "total_tokens": 13,
    "prompt_tokens_details": {
      "cached_tokens": 0
    }
  }
}
```

用于统计和首页展示的字段：

| 字段 | 来源 |
|---|---|
| 输入 token | `usage.prompt_tokens` |
| 输出 token | `usage.completion_tokens` |
| 缓存命中 token | `usage.prompt_tokens_details.cached_tokens` |
| 输出预览 | `choices[0].message` |
| 输入预览 | 请求体的 `messages` |

### 流式响应

请求：

```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "Tell me a story"}
  ],
  "stream": true
}
```

后端 SSE chunk 会原样转发给客户端。典型事件：

```text
data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}

data: {"choices":[{"delta":{"content":"Hel"},"index":0}]}

data: {"choices":[{"delta":{"content":"lo"},"index":0}],"usage":{"prompt_tokens":8,"completion_tokens":2}}

data: [DONE]
```

代理会在流结束后解析已转发的 SSE 行，用于统计和首页展示：

- 从 `usage.prompt_tokens` 记录输入 token。
- 从 `usage.completion_tokens` 记录输出 token。
- 从 `usage.prompt_tokens_details.cached_tokens` 记录缓存命中。
- 拼接 `choices[0].delta.content` 作为输出预览。
- 拼接 `choices[0].delta.tool_calls[*].function.name` 和 `arguments` 作为工具调用预览。

兼容性注意：部分后端的流式 chunk 中 `usage` 可能一直是 `null`，并且只在非流式响应中返回 token usage。这种情况下代理仍会原样转发流式内容，但首页统计中的 token 字段可能为空。

## Responses API

### 请求

端点：

```http
POST /responses
POST /v1/responses
```

典型请求：

```json
{
  "model": "gpt-4o",
  "input": "Hello, who are you?",
  "max_output_tokens": 1024,
  "stream": false
}
```

关键字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string | 必填，用于匹配模型配置 |
| `input` | string / array / object | Responses API 输入内容，代理原样转发 |
| `stream` | boolean | 可选，`true` 时走 SSE 流式转发 |
| `max_output_tokens` | integer | 可选；如果客户端未传且模型配置了 `max_tokens`，代理会自动注入到此字段 |

注意：two-API 当前是 Responses API 透传，不会把 Responses 请求转换成 Chat Completions 请求。如果上游 OpenAI 兼容后端不支持 `/responses` 或 `/v1/responses`，会返回后端自己的错误。

### 非流式响应

后端响应原样返回。典型格式：

```json
{
  "id": "resp_xxx",
  "object": "response",
  "model": "gpt-4o",
  "output_text": "Hello!",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {"type": "output_text", "text": "Hello!"}
      ]
    }
  ],
  "usage": {
    "input_tokens": 10,
    "output_tokens": 3,
    "total_tokens": 13,
    "input_tokens_details": {
      "cached_tokens": 0
    }
  }
}
```

部分兼容后端可能没有 `output_text`，只返回 `output`。例如：

```json
{
  "id": "resp_xxx",
  "object": "response",
  "status": "incomplete",
  "incomplete_details": {"reason": "length"},
  "model": "deepseek-v4-flash",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {"type": "output_text", "text": "ok"}
      ],
      "status": "incomplete"
    }
  ],
  "usage": {
    "input_tokens": 11,
    "output_tokens": 16,
    "total_tokens": 27,
    "input_tokens_details": {"cached_tokens": 0},
    "output_tokens_details": {"reasoning_tokens": 6}
  }
}
```

用于统计和首页展示的字段：

| 字段 | 来源 |
|---|---|
| 输入 token | `usage.input_tokens` |
| 输出 token | `usage.output_tokens` |
| 缓存命中 token | `usage.input_tokens_details.cached_tokens` |
| 输出预览 | `output_text`，没有时使用 `output` |
| 输入预览 | 请求体的 `input` |

### 流式响应

请求：

```json
{
  "model": "gpt-4o",
  "input": "Tell me a story",
  "stream": true
}
```

后端 SSE chunk 会原样转发给客户端。代理识别以下事件用于统计和预览：

```text
data: {"type":"response.output_text.delta","delta":"Hel"}

data: {"type":"response.output_text.delta","delta":"lo"}

data: {"type":"response.completed","response":{"usage":{"input_tokens":8,"output_tokens":2}}}
```

统计规则：

- 拼接 `type == "response.output_text.delta"` 的 `delta` 作为输出预览。
- 从事件顶层 `usage` 或 `response.usage` 读取 token。
- 从 `usage.input_tokens` 记录输入 token。
- 从 `usage.output_tokens` 记录输出 token。
- 从 `usage.input_tokens_details.cached_tokens` 记录缓存命中。

## Models

端点：

```http
GET /models
GET /v1/models
```

响应格式：

```json
{
  "object": "list",
  "data": [
    {"id": "gpt-4o", "object": "model"},
    {"id": "gpt-4o-mini", "object": "model"}
  ]
}
```

只列出当前配置中可用于 OpenAI endpoint 的模型名。

## Embeddings

### 请求

端点：

```http
POST /embeddings
```

典型请求：

```json
{
  "model": "text-embedding-3-small",
  "input": "Hello world"
}
```

代理行为：

- 使用 `model` 做 OpenAI 路由。
- 应用全局 `alias` 和模型名映射。
- 请求体原样转发给后端。
- 如果客户端未传 `max_tokens` 且模型配置了 `max_tokens`，当前实现也会注入 `max_tokens`。

### 响应

后端响应原样返回。典型格式：

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.01, -0.02, 0.03]
    }
  ],
  "model": "text-embedding-3-small",
  "usage": {
    "prompt_tokens": 2,
    "total_tokens": 2
  }
}
```
