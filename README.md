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

## 快速开始

复制 `config.yaml.example` 为 `~/.two-api/config.yaml`（首次使用需 `mkdir -p ~/.two-api`），编辑模型和后端信息：

```yaml
server:
  host: "0.0.0.0"
  port: 8080

models:
  - names:
      - gpt-4o
    openai_base_url: https://api.openai.com/v1
    api_key: sk-your-openai-key

  - names:
      - claude-sonnet-4-6
    anthropic_base_url: https://api.anthropic.com/v1
    api_key: sk-ant-your-anthropic-key

logging:
  level: INFO
  output: file
  dir: ~/.two-api/logs
```

启动：

```bash
two-api
```

详细配置说明见 [docs/config.md](docs/config.md)。

## Web 配置

启动后访问 `http://localhost:8080/settings` 可在浏览器中编辑配置。支持：

- 增删模型条目，编辑 names、后端地址、API Key、max_tokens、Responses→Chat 开关
- 管理全局别名和缓存规则
- 保存后热加载生效，无需重启

## 测试

运行测试：

```bash
python -m pytest -v
```

**Chat Completions：**

```bash
curl http://0.0.0.0:8080/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

**Anthropic Messages：**

```bash
curl http://0.0.0.0:8080/messages \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4-6", "max_tokens": 100, "messages": [{"role": "user", "content": "Hello"}]}'
```

更多测试示例见 [docs/config.md](docs/config.md)。

## 端点一览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 主页，展示配置、小时用量图和最近请求 |
| `/chat/completions` | POST | OpenAI 兼容 Chat Completions |
| `/responses`、`/v1/responses` | POST | OpenAI 兼容 Responses API |
| `/models`、`/v1/models` | GET | 列出可用 OpenAI 兼容模型 |
| `/embeddings` | POST | OpenAI 兼容向量接口 |
| `/messages`、`/v1/messages` | POST | Anthropic 兼容对话接口 |
| `/recent/download` | GET | 下载最近请求记录（支持 `?i=N` 下载单条） |

## 项目结构

```
two-API/
├── config.yaml.example      # 配置参考
├── pyproject.toml
├── README.md
├── docs/
│   └── README.en.md         # 英文文档
├── src/
│   ├── main.py              # FastAPI 入口 + 主页 HTML
│   ├── cli.py               # CLI 启动入口
│   ├── config.py            # YAML 配置 + Pydantic 校验
│   ├── cache.py             # LLM 响应缓存
│   ├── router.py            # 模型名 → 后端匹配
│   ├── forwarder.py         # httpx 转发 + 流式
│   ├── stats.py             # 线程安全统计 + 小时用量持久化 + 最近请求记录
│   ├── logging_setup.py     # structlog 配置
│   └── handlers/
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
