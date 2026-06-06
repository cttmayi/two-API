# 让 Codex 用上 DeepSeek：Responses API 和 Chat API 不兼容时的一个解决办法

如果你试过把 Codex CLI 接到 DeepSeek 或其他 OpenAI-compatible 服务，大概率会遇到一个很烦的问题：**模型明明能用，但 Codex 调不起来。**

原因通常不是模型能力，而是接口协议不一致：

- Codex 这类客户端越来越依赖 OpenAI 的 **Responses API**；
- 很多 DeepSeek/OpenAI-compatible 接入只提供或主要兼容 **Chat Completions API**；
- 客户端要 `/responses`，后端只认 `/chat/completions`。

这时候只改 base URL 往往不够。即使后端返回了内容，Codex 也可能因为工具调用、流式事件或 usage 字段格式不匹配，看起来像“没有反应”。

比较省事的办法是在中间加一层本地代理：Codex 继续请求 Responses API，代理把请求转成 Chat Completions 发给 DeepSeek，再把返回结果转回 Responses 格式。

这篇文章记录一个可直接使用的工具：`two-api`。它不只是解决 Codex + DeepSeek 这一个问题，更适合用来统一管理不同 LLM 后端的 API 入口。如果你只是想快速跑起来，可以直接收藏后按步骤配置。

## 问题背景：Responses 和 Chat 不是简单改路径

很多人第一反应是：既然都是 OpenAI-compatible，把路径从：

```text
/responses
```

改成：

```text
/chat/completions
```

是不是就行？

通常不行。

Responses API 和 Chat Completions API 的差异不只是 URL：

| 能力 | Responses API | Chat Completions API |
|---|---|---|
| 输入字段 | `input` | `messages` |
| 系统指令 | `instructions` | `system` message |
| 最大输出 | `max_output_tokens` | `max_tokens` |
| 工具定义 | Responses tool 格式 | Chat tool 格式 |
| 工具调用历史 | `function_call` / `function_call_output` item | assistant `tool_calls` + tool message |
| 流式输出 | `response.*` 事件 | Chat chunk delta |

因此要稳定兼容 Codex，至少要处理：

1. `input` 转 `messages`
2. `instructions` 转 `system` message
3. `max_output_tokens` 转 `max_tokens`
4. function tools 格式转换
5. 工具调用历史转换
6. Chat 响应转回 Responses 输出
7. Chat 流式 chunk 转 Responses stream events

`two-api` 的 `responses_to_chat` 就是为这个场景准备的。

## two-api 是什么？

`two-api` 是一个透明 LLM API 代理，可以按模型名把请求路由到不同后端，支持 OpenAI-compatible 和 Anthropic-compatible API。

简单说，它放在客户端和模型服务商之间：

```text
Codex / OpenAI SDK
        ↓
     two-api
        ↓
DeepSeek / Volcengine / OpenAI-compatible backend
```

当启用 `responses_to_chat` 后，链路变成：

```text
客户端请求 /responses
        ↓
two-api 转成 /chat/completions
        ↓
后端 Chat Completions
        ↓
two-api 转回 Responses 格式
        ↓
客户端继续认为自己在使用 Responses API
```

这样 Codex 不需要改客户端逻辑，后端也不需要真的支持 Responses API。

## 为什么推荐 two-api？

如果只是临时写一个脚本，把 `/responses` 转成 `/chat/completions` 也不是不行。但真实使用中，问题往往不止一个接口路径：

- 不同客户端使用的协议不一样；
- 不同服务商的 base URL 不一样；
- 同一个模型可能要同时兼容 OpenAI 和 Anthropic 风格接口；
- 有时还需要模型别名，比如客户端统一写 `default`，实际后端可以随时切换；
- 调试时需要看到最近请求、状态码、输入输出和 token 使用情况。

`two-api` 的定位就是做一个本地 LLM API 代理，把这些差异集中到一个配置文件里。对客户端来说，它只需要连一个统一入口；对后端来说，仍然按各自支持的协议工作。

这也是它适合推广给 Codex 用户的原因：你不需要等所有服务商都跟上 Responses API，也不需要每个客户端单独写适配层。

## 第一步：安装 two-api

推荐使用 pipx 安装，这样命令行工具会放在独立环境里，不影响系统 Python：

```bash
pipx install git+https://github.com/cttmayi/two-API.git
```

如果你已经在自己的 Python 虚拟环境里，也可以直接用 pip：

```bash
pip install git+https://github.com/cttmayi/two-API.git
```

安装完成后，会得到一个命令：

```bash
two-api
```

## 第二步：配置 DeepSeek 后端

创建配置目录：

```bash
mkdir -p ~/.two-api
```

新建 `~/.two-api/config.yaml`：

```yaml
server:
  host: "127.0.0.1"
  port: 8080

models:
  - names:
      - deepseek-v4-pro
      - deepseek-v4-flash
    openai_base_url: https://api.deepseek.com
    anthropic_base_url: https://api.deepseek.com/anthropic
    api_key: your-deepseek-api-key
    max_tokens: 8192
    responses_to_chat: true

alias:
  default: deepseek-v4-flash

logging:
  level: INFO
  output: file
  dir: ~/.two-api/logs
```

这里最重要的是两处：

```yaml
openai_base_url: https://api.deepseek.com
responses_to_chat: true
```

前者指向 DeepSeek 的 OpenAI-compatible Chat 接口，后者表示：当客户端访问 OpenAI Responses API 时，如果匹配到这个模型，就不要直接转发 `/responses`，而是转换成 `/chat/completions` 发给后端。

实际使用时，把 `api_key` 换成自己的 DeepSeek API key 即可。

如果你的后端地址不是 `/v1`，按服务商实际要求填写完整前缀。例如某些服务可能是：

```yaml
openai_base_url: https://example.com/api/v3
```

代理会把 Chat 路径拼到这个 base URL 后面。

## 第三步：启动本地代理

```bash
two-api ~/.two-api/config.yaml
```

或者直接：

```bash
two-api
```

默认读取 `~/.two-api/config.yaml`。

启动后，代理监听：

```text
http://127.0.0.1:8080
```

## 第四步：先用 curl 验证代理是否可用

可以先用 curl 验证：

```bash
curl http://127.0.0.1:8080/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v4-flash",
    "input": "请用一句话介绍你自己"
  }'
```

如果配置正确，客户端请求的是 `/responses`，后端实际收到的是 `/chat/completions`。

再测试流式：

```bash
curl http://127.0.0.1:8080/responses \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-v4-flash",
    "input": "写一个 Python hello world",
    "stream": true
  }'
```

## 第五步：配置 Codex 使用 two-api

Codex 侧需要做两件事：

1. 模型名填 `two-api` 配置里暴露出来的模型名，例如 `deepseek-v4-flash`；
2. provider 的 `base_url` 指向本地 `two-api`，并明确使用 Responses 协议。

可以参考下面这段配置：

```toml
model = "deepseek-v4-flash"
model_provider = "local"

[model_providers.local]
name = "local-api"
base_url = "http://127.0.0.1:8080"
api_key = "sk-xxxx"
wire_api = "responses"
```

这里几个字段最关键：

- `model = "deepseek-v4-flash"`：Codex 请求里使用的模型名，需要能匹配 `two-api` 的 `models.names` 或 `alias`；
- `model_provider = "local"`：让 Codex 使用下面定义的本地 provider；
- `base_url = "http://127.0.0.1:8080"`：指向刚启动的 `two-api`；
- `wire_api = "responses"`：告诉 Codex 仍然按 Responses API 发请求；
- `api_key = "sk-xxxx"`：这里可以填一个占位值，真正访问 DeepSeek 的 key 在 `two-api` 的 `config.yaml` 里配置。

如果你希望 Codex 里始终写同一个模型名，也可以使用 `two-api` 的 alias。例如 Codex 配：

```toml
model = "default"
```

然后在 `~/.two-api/config.yaml` 中集中切换：

```yaml
alias:
  default: deepseek-v4-flash
```

这样以后换后端模型，不一定要改 Codex 配置，只改代理配置即可。

## 配好之后，two-api 会自动做什么？

启用 `responses_to_chat` 后，你不需要手动改 Codex 请求，也不需要自己写转换脚本。代理会自动处理这些兼容问题：

- 把客户端的 `/responses` 请求转发到后端的 `/chat/completions`
- 把 `input` 转成 Chat 的 `messages`
- 把 `instructions` 转成 system message
- 把 `max_output_tokens` 转成 `max_tokens`
- 转换 function tools
- 保留 Codex 的工具调用历史和工具执行结果
- 把后端 Chat 文本响应转回 Responses 格式
- 把后端 Chat 流式输出转回 Responses 流式事件

也就是说，你配置好后，Codex 仍然按 Responses API 使用；后端仍然只需要支持 Chat Completions。

## Dashboard：排查问题很有用

访问：

```text
http://127.0.0.1:8080/
```

可以看到一个简单 dashboard，包括：

- 当前模型配置
- 最近请求
- 输入/输出预览
- 状态码
- token usage
- 流式与非流式请求记录

调 Codex 时如果发现“模型有返回但客户端没反应”，可以先看最近请求里：

1. 代理转给后端的 Chat request 长什么样；
2. 后端返回的状态码是不是 200；
3. output 里是否有内容或工具调用；
4. 流式事件是否完整。

这比盲猜客户端或服务商问题高效很多。

## 适用场景

如果你有下面任意一种需求，`two-api` 都比较适合放在工具箱里：

- Codex / 新版 OpenAI SDK 客户端要求 Responses API；
- 后端只有 Chat Completions API；
- 想接入 DeepSeek、Volcengine 或其他 OpenAI-compatible 服务；
- 同时使用 OpenAI-compatible 和 Anthropic-compatible 客户端；
- 希望用 alias 管理模型，例如客户端永远写 `default`；
- 希望在本地 dashboard 里看到最近请求、token 使用量和错误响应；
- 想用一个本地代理统一管理多个模型和后端。

它不适合所有情况：如果后端已经完整支持 Responses API，或者你不希望本地多跑一个代理进程，那就没必要额外加这一层。

## 总结

Codex 需要 Responses API，而很多 DeepSeek/OpenAI-compatible 后端只有 Chat Completions API，这是一个典型的“客户端协议”和“服务端协议”不一致问题。

解决思路不是简单改 URL，而是在中间做一次协议转换：

```text
Responses API  →  Chat Completions API  →  Responses API
```

`two-api` 的 `responses_to_chat` 配置就是为这个场景准备的。它可以让 Codex 继续使用 Responses API，同时把请求转给只支持 Chat 的后端。

更重要的是，`two-api` 可以把多模型、多服务商、多协议的适配工作收敛到一个本地代理里。今天可以用它解决 Codex 接 DeepSeek 的问题，之后也可以用同一套配置方式管理其他 OpenAI-compatible 或 Anthropic-compatible 后端。

如果你也遇到 Codex 接 DeepSeek 时卡在接口格式上的问题，可以按上面的配置试一下。整套流程只需要安装代理、写一个配置文件、把 Codex 的 base URL 指向本地代理。