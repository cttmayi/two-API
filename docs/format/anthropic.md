# Anthropic 兼容格式

本文说明 two-API 对 Anthropic 兼容 Messages API 的请求、响应和流式事件格式处理。

two-API 当前支持：

- `POST /messages`
- `POST /v1/messages`

## 通用代理行为

### 路由字段

Anthropic 兼容接口依赖请求体中的 `model` 字段路由：

```json
{
  "model": "claude-sonnet-4-6"
}
```

处理顺序：

1. 读取 JSON 请求体。
2. 检查 `model` 是否存在。
3. 如果配置了全局 `alias`，先把客户端传入的模型名替换成真实模型名。
4. 在配置的 Anthropic 模型组中查找该模型。
5. 如果模型配置里使用了 `names` 映射，例如 `{ "sonnet": "claude-sonnet-4-6" }`，转发给后端前会把 body 中的 `model` 改成后端模型名。
6. 如果客户端未传 `max_tokens` 且模型配置了 `max_tokens`，代理会自动注入。
7. 原样转发请求到 `anthropic_base_url + 当前请求路径`。

### 路径拼接细节

后端 URL 的构造方式是：

```text
anthropic_base_url.rstrip("/") + 当前请求路径
```

因此 `anthropic_base_url` 是否已经包含 `/v1` 会影响应该调用哪个本地端点：

| 配置 | 本地请求 | 实际后端路径 |
|---|---|---|
| `anthropic_base_url: https://api.example.com/v1` | `/messages` | `/v1/messages` |
| `anthropic_base_url: https://api.example.com/v1` | `/v1/messages` | `/v1/v1/messages` |
| `anthropic_base_url: https://api.example.com` | `/v1/messages` | `/v1/messages` |

如果 base URL 已经带 `/v1`，通常应调用 two-API 的无 `/v1` 路径，例如 `/messages`；如果 base URL 不带 `/v1`，才适合调用 `/v1/messages`。

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
  "error": "Unknown model: claude-unknown"
}
```

模型存在但只配置了 OpenAI 后端：

```json
{
  "error": "Model 'gpt-4o' not available on this endpoint"
}
```

后端连接失败或超时：

```json
{
  "error": "Backend unreachable"
}
```

后端返回非 200 时，two-API 不改写后端响应 body，会原样返回给客户端；同时会把完整响应文本保存到最近请求记录里用于调试。

## Messages

### 请求

端点：

```http
POST /messages
POST /v1/messages
```

典型请求：

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "messages": [
    {
      "role": "user",
      "content": "Hello"
    }
  ],
  "stream": false
}
```

关键字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string | 必填，用于匹配模型配置 |
| `max_tokens` | integer | Anthropic Messages API 常用必填字段；如果客户端未传且模型配置了 `max_tokens`，代理会自动注入 |
| `messages` | array | 对话消息列表，原样转发给后端 |
| `stream` | boolean | 可选，`true` 时走 SSE 流式转发 |
| `system` | string / array | 可选，系统提示，代理原样转发 |
| `tools` | array | 可选，工具定义，代理原样转发 |
| `tool_choice` | object / string | 可选，工具选择策略，代理原样转发 |

除 `model` 改写和 `max_tokens` 注入外，其他字段由代理原样转发。

### 输入消息格式

纯文本消息：

```json
{
  "role": "user",
  "content": "Hello"
}
```

内容块消息：

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "Describe this image"},
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": "..."
      }
    }
  ]
}
```

工具结果消息：

```json
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "toolu_123",
      "content": "result text"
    }
  ]
}
```

## 非流式响应

后端响应会原样返回。典型格式：

```json
{
  "id": "msg_123",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-6",
  "content": [
    {
      "type": "text",
      "text": "Hello!"
    }
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 10,
    "output_tokens": 3,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

用于统计和首页展示的字段：

| 字段 | 来源 |
|---|---|
| 输入 token | `usage.input_tokens` |
| 输出 token | `usage.output_tokens` |
| 缓存写入 token | `usage.cache_creation_input_tokens` |
| 缓存读取 token | `usage.cache_read_input_tokens` |
| 输出预览 | `content` |
| 输入预览 | 请求体的 `messages` |

## 工具调用响应

Anthropic 工具调用通常出现在响应 `content` 中：

```json
{
  "id": "msg_123",
  "type": "message",
  "role": "assistant",
  "content": [
    {
      "type": "text",
      "text": "I will check that."
    },
    {
      "type": "tool_use",
      "id": "toolu_123",
      "name": "get_weather",
      "input": {
        "city": "Shanghai"
      }
    }
  ],
  "stop_reason": "tool_use",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 20
  }
}
```

代理不执行工具，只负责转发请求和响应；工具调用内容会保存在最近请求记录中用于首页展示。

## 流式响应

请求：

```json
{
  "model": "claude-sonnet-4-6",
  "max_tokens": 1024,
  "messages": [
    {"role": "user", "content": "Tell me a story"}
  ],
  "stream": true
}
```

后端 SSE chunk 会原样转发给客户端。典型事件：

```text
event: message_start
data: {"type":"message_start","message":{"id":"msg_123","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-6","usage":{"input_tokens":8,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":2}}

event: message_stop
data: {"type":"message_stop"}
```

代理会在流结束后解析已转发的 SSE 行，用于统计和首页展示：

- 从事件 `usage.input_tokens` 记录输入 token。
- 从事件 `usage.output_tokens` 记录输出 token。
- 从事件 `usage.cache_read_input_tokens` 记录缓存读取。
- 从事件 `usage.cache_creation_input_tokens` 记录缓存写入。
- 根据 `content_block_start` 和 `content_block_delta` 重建输出内容块。

### 流式文本内容块

代理识别：

```text
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}
```

重建后的首页输出预览：

```json
[
  {
    "type": "text",
    "text": "Hello"
  }
]
```

### 流式工具调用内容块

代理识别：

```text
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_123","name":"get_weather","input":{}}}

data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\"city\":"}}

data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\"Shanghai\"}"}}
```

重建后的首页输出预览：

```json
[
  {
    "type": "tool_use",
    "id": "toolu_123",
    "name": "get_weather",
    "input": {
      "city": "Shanghai"
    }
  }
]
```

如果 `partial_json` 不能解析成合法 JSON，代理会保留原始字符串作为 `input`。

## 与 OpenAI 格式的主要差异

| 项目 | Anthropic Messages | OpenAI Chat Completions |
|---|---|---|
| 端点 | `/messages`, `/v1/messages` | `/chat/completions` |
| 输入消息字段 | `messages` | `messages` |
| 输出内容字段 | `content` 数组 | `choices[0].message` |
| 最大输出 token | `max_tokens` | `max_tokens` |
| token usage | `input_tokens`, `output_tokens` | `prompt_tokens`, `completion_tokens` |
| 缓存字段 | `cache_read_input_tokens`, `cache_creation_input_tokens` | `prompt_tokens_details.cached_tokens` |
| 工具调用输出 | `content[].type == "tool_use"` | `choices[0].message.tool_calls` 或流式 `delta.tool_calls` |
