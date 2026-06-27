# Config 配置引导

在 two-API dashboard 中内置一个 Web 配置页面，通过结构化表单编辑所有配置项，保存后热加载生效。

## 架构

```
Browser ──GET /──→ FastAPI dashboard HTML
         ──GET /settings──→ 配置页面（HTML + JS）
                │
                ├── GET  /api/config  → JSON（当前配置）
                └── POST /api/config  → JSON（新配置）
                        │
                        ├── Pydantic 校验
                        ├── 写入 YAML 到 ~/.two-api/config.yaml
                        └── 热加载：
                            ├── app.state.config = new_config
                            ├── app.state.router = ModelRouter(...)
                            └── init_cache(CacheConfig(...))
```

## API 层

### GET /api/config

返回当前配置的 JSON 序列化。`api_key` 做遮盖处理（如 `sk-****`），避免在浏览器中暴露密钥。

### POST /api/config

接收配置 JSON，使用 Pydantic `Config` 模型校验。校验失败返回 `422` + 错误详情。校验通过：

1. 序列化为 YAML 写入 `~/.two-api/config.yaml`
2. 更新 `app.state.config`
3. 重建 router：`app.state.router = ModelRouter(new_config.models)`
4. 重新初始化 cache：`init_cache(CacheConfig(...))`（清空旧缓存）
5. 日志配置不热加载（需重启生效）
6. 返回 `{ "status": "ok" }`

API key 处理逻辑：
- `GET /api/config` 返回遮盖后的 key（保留前 3 个字符，如 `sk-****`）
- `POST /api/config`：如果 key 字段值是遮盖后的字符串，则保留当前配置中的原值；如果是新值，则使用新值

## 前端

### 导航

`/` 和 `/settings` 页面共享导航栏，包含 "Dashboard" 和 "Settings" 链接。通过标准的 `<a>` 链接切换（整页加载）。

### 表单分区（同一页面滚动浏览）

**Server：**
- host：文本输入框
- port：数字输入框

**Models（动态列表，可增删行）：**
每行包含：
- `names`：可编辑的字符串列表 + 别名映射（标签式输入，可增删）
- `openai_base_url`：文本输入框
- `anthropic_base_url`：文本输入框
- `api_key`：文本输入框（可切换密码模式）
- `max_tokens`：数字输入框，可选
- `responses_to_chat`：开关

底部有 "Add Model" 按钮追加空行，每行有删除按钮。

**Alias（键值对列表，动态增删）：**
- 每行：key 输入框 + value 输入框
- 增删按钮

**Logging：**
- level：下拉框（DEBUG、INFO、WARNING、ERROR）
- output：下拉框（file、console）
- dir：文本输入框

**Cache：**
- enabled：开关
- ttl_seconds：数字输入框
- max_entries：数字输入框
- aliases：标签式输入列表
- key_fields：标签式输入列表

### 操作按钮

- **Save**：收集表单数据，POST 到 `/api/config`，显示成功/失败提示。成功后可选跳转到 dashboard。
- **Cancel**：重置表单到上次保存的状态（重新请求 GET /api/config）。

### 客户端校验（提交前）

- Port 必须在 1-65535 之间
- 至少需要一个 model 条目
- 每个 model 条目必须至少有一个 name 和一个 base URL
- Alias 的 key 不能为空
- Logging level 必须是有效值

## 热加载行为

- Router 和 cache 立即更新，后续请求使用新配置
- 正在处理中的请求不受影响（继续使用旧配置）
- Cache 在配置变更时清空（TTL/alias 规则可能已变化）
- 日志配置变更需要重启（不支持热加载）

## 涉及文件

| 文件 | 变更 |
|------|------|
| `src/main.py` | 新增 `/api/config` GET/POST 端点、`/settings` 页面、两个页面的导航栏 |
| `pyproject.toml` | 无变更（PyYAML 已经是依赖） |

## 测试

- `GET /api/config` 返回有效 JSON，结构匹配当前配置
- `POST /api/config` 合法数据写入文件并更新 app state
- `POST /api/config` 非法数据返回 422
- API key 遮盖后提交能正确保留原值
- 导航栏在两个页面都正确显示
- 前端模型行增删功能正常
- Cache 热加载清空旧缓存
