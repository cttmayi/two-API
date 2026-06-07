from contextlib import asynccontextmanager
import html as _html
import json
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from src.config import load_config
from src.router import ModelRouter
from src.logging_setup import setup_logging
from src.stats import get_stats, init_stats


def usage_path_for_log_dir(log_dir: str) -> str:
    return os.path.join(os.path.dirname(os.path.expanduser(log_dir)), "usage.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    config = load_config("~/.two-api/config.yaml")
    app.state.config = config
    app.state.router = ModelRouter(config.models)
    log_path = setup_logging(config.logging.dir, config.logging.level)
    app.state.log_path = log_path
    init_stats(usage_path_for_log_dir(config.logging.dir))
    import structlog
    logger = structlog.get_logger()
    logger.info("proxy_startup", log_path=log_path, host=config.server.host, port=config.server.port)
    yield
    # Shutdown
    from src.forwarder import get_forward_client
    client = get_forward_client()
    await client.aclose()


app = FastAPI(lifespan=lifespan)


def _fmt(n: int | None) -> str:
    if n is None or n == 0:
        return "0"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


@app.head("/")
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    config = request.app.state.config
    stats = get_stats().snapshot()

    config_rows = ""
    for entry in config.models:
        backends = []
        if entry.openai_base_url:
            backends.append(f'<span class="proto-tag">OpenAI</span> {entry.openai_base_url}')
        if entry.anthropic_base_url:
            backends.append(f'<span class="proto-tag">Anthropic</span> {entry.anthropic_base_url}')
        backends_html = "<br>".join(backends) if backends else "&mdash;"
        has_key = '<span class="tag tag-yes">Yes</span>' if entry.api_key else '<span class="tag tag-no">No</span>'
        name_pairs = list(entry.get_name_map().items())
        rowspan = len(name_pairs)
        for i, (client_name, backend_name) in enumerate(name_pairs):
            same = "same" if client_name == backend_name else ""
            config_rows += f"""
        <tr>
            <td class="model-name-cell">{client_name}</td>
            <td class="backend-name-cell {same}">{backend_name}</td>"""
            if i == 0:
                config_rows += f"""
            <td class="backend-cell" rowspan="{rowspan}">{backends_html}</td>
            <td class="key-cell" rowspan="{rowspan}">{has_key}</td>"""
            config_rows += """
        </tr>"""

    stats_cards = ""
    for name, m in stats.get("models", {}).items():
        avg_lat = m["total_latency_ms"] / m["requests"] if m["requests"] else 0
        tok_sec = m["completion_tokens"] * 1000 / m["total_latency_ms"] if m["completion_tokens"] and m["total_latency_ms"] else 0
        stats_cards += f"""
        <div class="model-card">
            <div class="model-card-header">
                <span class="model-name">{name}</span>
                <span class="model-provider">{m["provider"]}</span>
            </div>
            <div class="model-card-body">
                <div class="metric">
                    <span class="metric-value">{m["requests"]}</span>
                    <span class="metric-label">Requests</span>
                </div>
                <div class="metric">
                    <span class="metric-value">{_fmt(m["prompt_tokens"])}</span>
                    <span class="metric-label">Prompt Tokens</span>
                </div>
                <div class="metric">
                    <span class="metric-value">{_fmt(m["completion_tokens"])}</span>
                    <span class="metric-label">Completion Tokens</span>
                </div>
                <div class="metric">
                    <span class="metric-value">{_fmt(m["cache_read_tokens"])}</span>
                    <span class="metric-label">Cache Read</span>
                </div>
                <div class="metric">
                    <span class="metric-value">{_fmt(m["cache_write_tokens"])}</span>
                    <span class="metric-label">Cache Write</span>
                </div>
                <div class="metric">
                    <span class="metric-value">{avg_lat:.0f}<span class="metric-unit"> ms</span></span>
                    <span class="metric-label">Avg Latency</span>
                </div>
                <div class="metric">
                    <span class="metric-value">{tok_sec:.0f}<span class="metric-unit"> tok/s</span></span>
                    <span class="metric-label">Tokens/s</span>
                </div>
            </div>
        </div>"""

    def _tool_input_summary(input_val):
        """Extract a short summary from a tool_use input dict, showing all fields."""
        if not isinstance(input_val, dict):
            return ""
        pairs = []
        for k, v in input_val.items():
            if isinstance(v, str) and v.strip():
                val = v.strip()[:60]
                pairs.append(k + "=" + val)
            elif isinstance(v, (int, float, bool)):
                pairs.append(k + "=" + str(v))
            elif isinstance(v, list):
                pairs.append(k + "=[...]")
            elif isinstance(v, dict):
                pairs.append(k + "={...}")
        return "(" + ", ".join(pairs) + ")" if pairs else ""


    recent_rows = ""
    for i, r in enumerate(stats.get("recent", [])):
        detail_id = f"detail-{i}"
        pt = r.get("prompt_tokens") or 0
        ct = r.get("completion_tokens") or 0
        cr = r.get("cache_read") or 0
        cw = r.get("cache_write") or 0
        status_cls = "status-ok" if (r.get("status") or 200) < 400 else "status-err"
        stream_badge = '<span class="stream-badge stream-yes">S</span>' if r.get("streaming") else '<span class="stream-badge stream-no">N</span>'

        # Extract input preview: last message content
        input_preview = ""
        messages = r.get("input_messages", [])
        if messages:
            last_msg = messages[-1]
            role = last_msg.get("role", "")
            content = last_msg.get("content", "")
            if isinstance(content, list):
                # Anthropic format: content blocks
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        t = block.get("type", "")
                        if t == "text":
                            parts.append(str(block.get("text", "")))
                        elif t == "tool_result":
                            parts.append("[tool_result: " + str(block.get("content", "")) + "]")
                        elif t == "tool_use":
                            parts.append("[tool_use: " + str(block.get("name", "")) + "]")
                        elif t == "image":
                            parts.append("[image]")
                input_preview = " ".join(parts)
            elif isinstance(content, str):
                input_preview = content
            if role and not input_preview.startswith("[" + role):
                input_preview = "[" + role + "] " + input_preview
        input_preview = _html.escape(input_preview[:120])

        # Extract output preview: text + tool calls
        output_preview = ""
        output = r.get("output")
        if isinstance(output, list):
            # Anthropic: list of content blocks
            parts = []
            for block in output:
                if isinstance(block, dict):
                    t = block.get("type", "")
                    if t == "text":
                        parts.append(str(block.get("text", "")))
                    elif t == "tool_use":
                        name = str(block.get("name", ""))
                        inp = _tool_input_summary(block.get("input"))
                        parts.append("[tool: " + name + "]" + inp)
            output_preview = " ".join(parts)
        elif isinstance(output, dict):
            # OpenAI: dict with content and optional tool_calls
            parts = []
            if output.get("content"):
                parts.append(str(output["content"]))
            if output.get("output_text"):
                parts.append(str(output["output_text"]))
            for tc in output.get("tool_calls", []):
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    fn_name = str(fn.get("name", ""))
                    inp = ""
                    args_str = fn.get("arguments", "")
                    if isinstance(args_str, str) and args_str.strip():
                        try:
                            args_obj = json.loads(args_str)
                            inp = _tool_input_summary(args_obj)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                    parts.append("[tool: " + fn_name + "]" + inp)
            output_preview = " ".join(parts)
        output_preview = _html.escape(output_preview[:120])

        input_json = _html.escape(json.dumps(r.get("request_body") or r.get("input_messages", []), ensure_ascii=False, indent=2))
        output_json = _html.escape(json.dumps(r.get("output"), ensure_ascii=False, indent=2))
        recent_rows += f"""
        <tr class="recent-row" onclick="toggleDetail('{detail_id}')">
            <td class="cell-time">{r.get("time", "")}</td>
            <td>{r.get("alias", "")}</td>
            <td>{r.get("model", "")}</td>
            <td>{r.get("provider", "")}</td>
            <td>{stream_badge}</td>
            <td class="{status_cls}">{r.get("status", "")}</td>
            <td class="cell-num">{r.get("latency_ms", "")}ms</td>
            <td class="cell-num">{_fmt(pt)}</td>
            <td class="cell-num">{_fmt(ct)}</td>
            <td class="cell-num">{_fmt(cr)}</td>
            <td class="cell-num">{_fmt(cw)}</td>
            <td class="cell-preview" title="{input_preview}">{input_preview}</td>
            <td class="cell-preview" title="{output_preview}">{output_preview}</td>
            <td class="cell-save" onclick="event.stopPropagation()"><a href="/recent/download?i={i}" class="save-btn" title="Save">&#128190;</a></td>
        </tr>
        <tr class="detail-row" id="{detail_id}" style="display:none;">
            <td colspan="14">
                <div class="detail-grid">
                    <div class="detail-block">
                        <div class="detail-label">INPUT</div>
                        <pre class="detail-json">{input_json}</pre>
                    </div>
                    <div class="detail-block">
                        <div class="detail-label">OUTPUT</div>
                        <pre class="detail-json">{output_json}</pre>
                    </div>
                </div>
            </td>
        </tr>"""

    uptime_m = stats["uptime_seconds"] // 60
    uptime_s = stats["uptime_seconds"] % 60
    total_requests = stats["total_requests"]
    model_count = len(config.models)
    if recent_rows:
        recent_section = '<table class="recent-table"><thead><tr><th>Time</th><th>Alias</th><th>Model</th><th>Provider</th><th>Stream</th><th>Status</th><th class="cell-num">Latency</th><th class="cell-num">Prompt</th><th class="cell-num">Completion</th><th class="cell-num">Cache Read</th><th class="cell-num">Cache Write</th><th>Input</th><th>Output</th><th></th></tr></thead><tbody>' + recent_rows + '</tbody></table>'
    else:
        recent_section = '<div class="empty-state">No requests processed yet</div>'
    hourly_json = json.dumps(stats.get("hourly", []), ensure_ascii=False).replace("</", "<\\/")

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>two-API Proxy</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f0f2f5;
    color: #1a1a2e;
    min-height: 100vh;
    padding: 2.5rem 3rem;
}}

header {{ margin-bottom: 2rem; }}
header h1 {{ font-size: 1.6rem; font-weight: 700; color: #1a1a2e; }}
header .subtitle {{ color: #999; font-size: 0.85rem; margin-top: 0.15rem; }}

.summary {{
    display: flex;
    gap: 1rem;
    margin-bottom: 2.5rem;
    flex-wrap: wrap;
}}
.summary-card {{
    background: #fff;
    border-radius: 10px;
    padding: 1.25rem 2rem;
    min-width: 150px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}}
.summary-card .value {{ font-size: 1.75rem; font-weight: 700; color: #1a1a2e; }}
.summary-card .label {{ font-size: 0.8rem; color: #999; text-transform: uppercase; letter-spacing: 0.4px; }}

.section {{ margin-bottom: 2.5rem; }}
.section-title {{
    font-size: 1.05rem;
    font-weight: 600;
    color: #444;
    margin-bottom: 0.85rem;
}}

/* Config table */
.config-table {{
    width: 100%;
    border-collapse: collapse;
    background: #fff;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    table-layout: fixed;
}}
.config-table th {{
    background: #f8f9fb;
    font-weight: 600;
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: #777;
    padding: 11px 20px;
    text-align: left;
}}
.config-table th:first-child {{ width: 18%; }}
.config-table th:nth-child(2) {{ width: 18%; }}
.config-table th:last-child {{ width: 9%; }}
.config-table td {{
    padding: 14px 20px;
    border-top: 1px solid #f0f0f3;
    vertical-align: middle;
}}
.config-table tr:hover td {{ background: #fafbfd; }}
.model-name-cell {{
    font-weight: 600;
    font-size: 0.9rem;
    color: #1a1a2e;
}}
.backend-name-cell {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.82rem;
    color: #333;
}}
.backend-name-cell.same {{
    color: #bbb;
}}
.backend-cell {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.82rem;
    color: #444;
    line-height: 1.7;
    word-break: break-all;
}}
.proto-tag {{
    display: inline-block;
    font-family: -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: #fff;
    background: #6b7280;
    padding: 1px 7px;
    border-radius: 4px;
    margin-right: 4px;
    vertical-align: middle;
}}
.key-cell {{ text-align: center; }}
.tag {{
    display: inline-block;
    padding: 3px 12px;
    border-radius: 20px;
    font-size: 0.75rem;
    font-weight: 600;
}}
.tag-yes {{ background: #e6f7e6; color: #2d8a2d; }}
.tag-no  {{ background: #fff0f0; color: #c44; }}

/* Stats cards */
.model-cards {{
    display: flex;
    flex-direction: column;
    gap: 0.85rem;
}}
.model-card {{
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    overflow: hidden;
}}
.model-card-header {{
    padding: 12px 20px;
    background: #f8f9fb;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid #f0f0f3;
}}
.model-name {{ font-weight: 700; font-size: 0.9rem; color: #1a1a2e; }}
.model-provider {{
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: #2563eb;
    background: #eef2ff;
    padding: 2px 10px;
    border-radius: 20px;
    font-weight: 600;
}}
.model-card-body {{
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 0;
}}
.model-card-body .metric {{
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 3px;
    border-right: 1px solid #f5f5f8;
}}
.model-card-body .metric:last-child {{ border-right: none; }}
.metric-value {{ font-size: 1.1rem; font-weight: 700; color: #1a1a2e; }}
.metric-unit {{ font-size: 0.7rem; font-weight: 500; color: #999; }}
.metric-label {{
    font-size: 0.72rem;
    color: #aaa;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}}
.hourly-panel {{
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    margin-top: 1rem;
    padding: 1rem 1.25rem;
}}
.hourly-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1rem;
}}
.hourly-controls {{
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    color: #777;
    font-size: 0.75rem;
}}
.hourly-controls select {{
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    background: #fff;
    color: #333;
    padding: 4px 8px;
    font-size: 0.75rem;
}}
.hourly-chart {{
    height: 220px;
    display: flex;
    align-items: flex-end;
    gap: 8px;
    border-left: 1px solid #eef0f5;
    border-bottom: 1px solid #eef0f5;
    padding: 16px 10px 24px 10px;
    overflow-x: auto;
}}
.hourly-bar-wrap {{
    min-width: 42px;
    flex: 1;
    max-width: 72px;
    height: 100%;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    align-items: center;
    gap: 6px;
}}
.hourly-bar-value {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.68rem;
    color: #888;
    min-height: 12px;
}}
.hourly-bar {{
    width: 100%;
    min-height: 2px;
    border-radius: 6px 6px 0 0;
    cursor: pointer;
    transition: opacity 0.15s, transform 0.15s;
    overflow: hidden;
    display: flex;
    flex-direction: column-reverse;
    position: relative;
}}
.hourly-bar:hover {{ opacity: 0.9; transform: translateY(-2px); }}
.hourly-segment {{ width: 100%; min-height: 2px; }}
.hourly-tooltip {{
    position: fixed;
    display: none;
    z-index: 10;
    min-width: 220px;
    max-width: 840px;
    white-space: nowrap;
    background: rgba(26,26,46,0.96);
    color: #fff;
    border-radius: 8px;
    padding: 10px 12px;
    font-size: 0.72rem;
    line-height: 1.5;
    pointer-events: none;
    box-shadow: 0 8px 24px rgba(0,0,0,0.18);
}}
.hourly-legend {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem 1rem;
    margin-top: 0.85rem;
}}
.hourly-legend-item {{ display: inline-flex; align-items: center; gap: 6px; color: #666; font-size: 0.75rem; }}
.hourly-legend-color {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; }}
.hourly-bar-label {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.68rem;
    color: #999;
    white-space: nowrap;
}}
.hourly-detail {{ margin-top: 1rem; }}
.hourly-detail-table-wrap {{ box-shadow: none; }}
.hourly-detail-name {{ font-weight: 700; color: #1a1a2e; word-break: break-word; }}
.hourly-detail-empty {{ color: #999; padding: 0.85rem 1rem; background: #f8f9fb; border-radius: 8px; }}

.empty-state {{
    text-align: center;
    padding: 3rem;
    color: #bbb;
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}}

/* Recent Requests */
.recent-table-wrap {{
    background: #fff;
    border-radius: 10px;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}}
.recent-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 0.82rem;
}}
.recent-table th {{
    background: #f8f9fb;
    font-weight: 600;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: #777;
    padding: 10px 12px;
    text-align: left;
    white-space: nowrap;
}}
.recent-table td {{
    padding: 9px 12px;
    border-top: 1px solid #f0f0f3;
    vertical-align: middle;
}}
.recent-row {{
    cursor: pointer;
    transition: background 0.15s;
}}
.recent-row:hover td {{
    background: #f5f7ff;
}}
.cell-time {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.78rem;
    color: #888;
}}
.cell-num {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.78rem;
    color: #444;
    text-align: right;
}}
.recent-table th.cell-num {{ text-align: right; }}
.cell-preview {{
    font-size: 0.78rem;
    color: #555;
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.cell-save {{ text-align: center; width: 40px; }}
.save-btn {{ text-decoration: none; font-size: 0.85rem; opacity: 0.5; transition: opacity 0.15s; }}
.save-btn:hover {{ opacity: 1; }}
.status-ok {{ color: #2d8a2d !important; font-weight: 600; }}
.status-err {{ color: #c44 !important; font-weight: 600; }}
.stream-badge {{
    display: inline-block;
    font-size: 0.65rem;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;
    text-align: center;
    min-width: 20px;
}}
.stream-yes {{ background: #eef2ff; color: #2563eb; }}
.stream-no {{ background: #f5f5f8; color: #999; }}
.detail-row td {{
    padding: 0;
    background: #fafbfd;
    border-top: 1px solid #e8e8ef;
}}
.detail-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
    padding: 1rem 1.25rem;
}}
.detail-block {{
    min-width: 0;
}}
.detail-label {{
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: #999;
    margin-bottom: 6px;
}}
.detail-json {{
    font-family: "SF Mono", "Fira Code", "Cascadia Code", monospace;
    font-size: 0.73rem;
    line-height: 1.5;
    color: #333;
    background: #fff;
    border: 1px solid #e8e8ef;
    border-radius: 6px;
    padding: 12px 14px;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
    max-height: 480px;
    overflow-y: auto;
    margin: 0;
}}

footer {{
    margin-top: 2.5rem;
    color: #ccc;
    font-size: 0.75rem;
}}
</style>
</head>
<body>

<header>
    <h1>two-API Proxy</h1>
    <div class="subtitle">LLM API Proxy &mdash; OpenAI &amp; Anthropic compatible</div>
</header>

<div class="summary">
    <div class="summary-card">
        <div class="value">{uptime_m}m {uptime_s}s</div>
        <div class="label">Uptime</div>
    </div>
    <div class="summary-card">
        <div class="value">{total_requests}</div>
        <div class="label">Total Requests</div>
    </div>
    <div class="summary-card">
        <div class="value">{model_count}</div>
        <div class="label">Model Groups</div>
    </div>
</div>

<div class="section">
    <div class="section-title" style="cursor:pointer; user-select:none;" onclick="toggleSection('config-body')">
        <span id="config-arrow" style="display:inline-block;transition:transform 0.2s;margin-right:6px;">&#9654;</span>Model Configuration
    </div>
    <div id="config-body" style="display:none;">
    <table class="config-table">
    <thead><tr><th>Model Name</th><th>Backend Name</th><th>Backend URLs</th><th>API Key</th></tr></thead>
    <tbody>{config_rows}</tbody>
    </table>
    </div>
</div>

<div class="section">
    <div class="section-title" style="cursor:pointer; user-select:none;" onclick="toggleSection('hourly-body')">
        <span id="hourly-arrow" style="display:inline-block;transition:transform 0.2s;margin-right:6px;transform: rotate(90deg);">&#9654;</span>Hourly Token Usage
    </div>
    <div id="hourly-body" style="display:block;">
    <div class="hourly-panel">
        <div class="hourly-header">
            <div></div>
            <div class="hourly-controls">
                <label for="hourly-group-by">Group by</label>
                <select id="hourly-group-by" onchange="setHourlyGroup(this.value)">
                    <option value="aliases" selected>ALIAS</option>
                    <option value="models">MODEL</option>
                </select>
            </div>
        </div>
        <div id="hourly-chart" class="hourly-chart"></div>
        <div id="hourly-tooltip" class="hourly-tooltip"></div>
        <div id="hourly-legend" class="hourly-legend"></div>
        <div id="hourly-detail" class="hourly-detail">Click a bar to view hourly details.</div>
    </div>
</div>
</div>

<div class="section">
    <div class="section-title" style="cursor:pointer; user-select:none;" onclick="toggleSection('recent-body')">
        <span id="recent-arrow" style="display:inline-block;transition:transform 0.2s;margin-right:6px;transform: rotate(90deg);">&#9654;</span>Recent Requests
    </div>
    <div id="recent-body" style="display:block;">
    <div class="recent-table-wrap">
        {recent_section}
    </div>
</div>
</div>

<script>
var hourlyUsageData = {hourly_json};
var hourlyGroupBy = "aliases";
function setHourlyGroup(value) {{
    hourlyGroupBy = value === "aliases" ? "aliases" : "models";
    renderHourlyChart();
    renderHourlyDetail();
}}
function fmtMetric(n) {{
    n = n || 0;
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "K";
    if (n % 1) return n.toFixed(1);
    return String(n);
}}
function fmtDuration(ms) {{
    ms = ms || 0;
    if (ms >= 1000) return (ms / 1000).toFixed(1) + "s";
    if (ms % 1) return ms.toFixed(1) + "ms";
    return String(ms) + "ms";
}}
function hourlyLabel(hour) {{
    return (hour || "").slice(11, 16) || hour;
}}
function colorForGroup(name) {{
    var colors = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#be123c", "#4f46e5", "#65a30d", "#c026d3"];
    var hash = 0;
    for (var i = 0; i < name.length; i++) hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
    return colors[Math.abs(hash) % colors.length];
}}
function hourlyGroups(item) {{
    var aliases = item.aliases || {{}};
    var groups = {{}};
    if (hourlyGroupBy === "aliases") {{
        Object.keys(aliases).forEach(function(alias) {{
            var total = {{ requests: 0, prompt_tokens: 0, completion_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 0, total_latency_ms: 0, avg_latency_ms: 0, latency_per_output_token_ms: 0, models: {{}} }};
            Object.keys(aliases[alias]).forEach(function(model) {{
                var d = aliases[alias][model];
                total.requests += d.requests || 0;
                total.prompt_tokens += d.prompt_tokens || 0;
                total.completion_tokens += d.completion_tokens || 0;
                total.cache_read_tokens += d.cache_read_tokens || 0;
                total.cache_write_tokens += d.cache_write_tokens || 0;
                total.total_tokens += d.total_tokens || 0;
                total.total_latency_ms += d.total_latency_ms || 0;
                total.models[model] = d;
            }});
            if (total.requests) {{
                total.avg_latency_ms = total.total_latency_ms / total.requests;
                if (total.completion_tokens) total.latency_per_output_token_ms = total.completion_tokens * 1000 / total.total_latency_ms;
            }}
            groups[alias] = total;
        }});
    }} else {{
        Object.keys(aliases).forEach(function(alias) {{
            Object.keys(aliases[alias]).forEach(function(model) {{
                var d = aliases[alias][model];
                if (!groups[model]) {{
                    groups[model] = {{ requests: 0, prompt_tokens: 0, completion_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 0, total_latency_ms: 0, avg_latency_ms: 0, latency_per_output_token_ms: 0, provider: d.provider || "" }};
                }}
                var g = groups[model];
                g.requests += d.requests || 0;
                g.prompt_tokens += d.prompt_tokens || 0;
                g.completion_tokens += d.completion_tokens || 0;
                g.cache_read_tokens += d.cache_read_tokens || 0;
                g.cache_write_tokens += d.cache_write_tokens || 0;
                g.total_tokens += d.total_tokens || 0;
                g.total_latency_ms += d.total_latency_ms || 0;
            }});
        }});
        Object.keys(groups).forEach(function(model) {{
            var g = groups[model];
            if (g.requests) g.avg_latency_ms = g.total_latency_ms / g.requests;
            if (g.completion_tokens && g.total_latency_ms) g.latency_per_output_token_ms = g.completion_tokens * 1000 / g.total_latency_ms;
        }});
    }}
    if (!Object.keys(groups).length && (item.requests || 0) > 0) {{
        groups[""] = {{
            requests: item.requests || 0,
            prompt_tokens: item.prompt_tokens || 0,
            completion_tokens: item.completion_tokens || 0,
            cache_read_tokens: item.cache_read_tokens || 0,
            cache_write_tokens: item.cache_write_tokens || 0,
            total_tokens: item.total_tokens || 0,
            total_latency_ms: item.total_latency_ms || 0,
            avg_latency_ms: item.avg_latency_ms || 0,
            latency_per_output_token_ms: item.latency_per_output_token_ms || 0,
            models: {{}},
        }};
    }}
    return groups;
}}
function groupLabel(name) {{
    return name;
}}
function groupNames() {{
    var names = {{}};
    hourlyUsageData.forEach(function(item) {{
        Object.keys(hourlyGroups(item)).forEach(function(name) {{ names[name] = true; }});
    }});
    return Object.keys(names).sort();
}}
function metricValue(item, metric) {{
    return item[metric] || 0;
}}
function groupMetricValue(item, name, metric) {{
    var groupData = hourlyGroups(item)[name] || {{}};
    return groupData[metric] || 0;
}}
function renderHourlyLegend() {{
    var legend = document.getElementById("hourly-legend");
    legend.innerHTML = "";
    groupNames().forEach(function(name) {{
        var item = document.createElement("span");
        item.className = "hourly-legend-item";
        item.innerHTML = '<span class="hourly-legend-color" style="background:' + colorForGroup(name) + '"></span>' + groupLabel(name);
        legend.appendChild(item);
    }});
}}
function tooltipHtml(item, metric) {{
    var rows = Object.keys(hourlyGroups(item)).sort().map(function(name) {{
        var data = hourlyGroups(item)[name] || {{}};
        var backend = hourlyGroupBy === "aliases" && data.model ? ' → ' + data.model : '';
        return '<div><span style="color:' + colorForGroup(name) + '">●</span> ' + groupLabel(name) + backend + ': ' +
            fmtMetric(data[metric]) + ' / total ' + fmtMetric(data.total_tokens) +
            ' (P ' + fmtMetric(data.prompt_tokens) + ', C ' + fmtMetric(data.completion_tokens) +
            ', CR ' + fmtMetric(data.cache_read_tokens) + ', CW ' + fmtMetric(data.cache_write_tokens) +
            ', Avg ' + fmtDuration(data.avg_latency_ms) + ', Out ' + fmtMetric(data.latency_per_output_token_ms) + ' tok/s)</div>';
    }}).join('');
    return '<strong>Token Details by ' + (hourlyGroupBy === "aliases" ? "Alias" : "Model") + '</strong><br>' +
        item.hour + '<br>' +
        'Requests: ' + fmtMetric(item.requests) + '<br>' +
        'Total Tokens: ' + fmtMetric(item[metric]) + '<br>' +
        'Cache Read: ' + fmtMetric(item.cache_read_tokens) + '<br>' +
        'Cache Write: ' + fmtMetric(item.cache_write_tokens) + '<br>' +
        'Avg Latency: ' + fmtDuration(item.avg_latency_ms) + '<br>' +
        'Tokens/s: ' + fmtMetric(item.latency_per_output_token_ms) + '<hr style="border:0;border-top:1px solid rgba(255,255,255,0.18);margin:6px 0">' + rows;
}}
function showTooltip(event, html) {{
    var tooltip = document.getElementById("hourly-tooltip");
    tooltip.innerHTML = html;
    tooltip.style.display = "block";
    tooltip.style.left = Math.min(event.clientX + 12, window.innerWidth - tooltip.offsetWidth - 12) + "px";
    tooltip.style.top = Math.min(event.clientY + 12, window.innerHeight - tooltip.offsetHeight - 12) + "px";
}}
function hideTooltip() {{
    document.getElementById("hourly-tooltip").style.display = "none";
}}
function groupedUsageSummary() {{
    var summary = {{}};
    hourlyUsageData.forEach(function(item) {{
        Object.keys(hourlyGroups(item)).forEach(function(name) {{
            var data = hourlyGroups(item)[name] || {{}};
            if (!summary[name]) {{
                summary[name] = {{
                    requests: 0,
                    prompt_tokens: 0,
                    completion_tokens: 0,
                    cache_read_tokens: 0,
                    cache_write_tokens: 0,
                    total_tokens: 0,
                    total_latency_ms: 0,
                }};
            }}
            summary[name].requests += data.requests || 0;
            summary[name].prompt_tokens += data.prompt_tokens || 0;
            summary[name].completion_tokens += data.completion_tokens || 0;
            summary[name].cache_read_tokens += data.cache_read_tokens || 0;
            summary[name].cache_write_tokens += data.cache_write_tokens || 0;
            summary[name].total_tokens += data.total_tokens || 0;
            summary[name].total_latency_ms += data.total_latency_ms || 0;
        }});
    }});
    return summary;
}}
function renderHourlyDetail() {{
    var detail = document.getElementById("hourly-detail");
    var rows = {{}};
    hourlyUsageData.forEach(function(item) {{
        Object.keys(item.aliases || {{}}).forEach(function(alias) {{
            Object.keys(item.aliases[alias] || {{}}).forEach(function(model) {{
                var d = item.aliases[alias][model];
                var key = alias + "\t" + model;
                if (!rows[key]) {{
                    rows[key] = {{ alias: alias, model: model, requests: 0, prompt_tokens: 0, completion_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 0, total_latency_ms: 0 }};
                }}
                var r = rows[key];
                r.requests += d.requests || 0;
                r.prompt_tokens += d.prompt_tokens || 0;
                r.completion_tokens += d.completion_tokens || 0;
                r.cache_read_tokens += d.cache_read_tokens || 0;
                r.cache_write_tokens += d.cache_write_tokens || 0;
                r.total_tokens += d.total_tokens || 0;
                r.total_latency_ms += d.total_latency_ms || 0;
            }});
        }});
    }});
    var sorted = Object.keys(rows).sort().map(function(key) {{
        var r = rows[key];
        var avg = r.requests ? r.total_latency_ms / r.requests : 0;
        var perOutput = r.completion_tokens && r.total_latency_ms ? r.completion_tokens * 1000 / r.total_latency_ms : 0;
        return '<tr class="hourly-detail-row">' +
            '<td class="hourly-detail-name">' + (r.alias || '') + '</td>' +
            '<td class="hourly-detail-name">' + r.model + '</td>' +
            '<td class="cell-num">' + fmtMetric(r.requests) + '</td>' +
            '<td class="cell-num">' + fmtMetric(r.total_tokens) + '</td>' +
            '<td class="cell-num">' + fmtMetric(r.prompt_tokens) + '</td>' +
            '<td class="cell-num">' + fmtMetric(r.completion_tokens) + '</td>' +
            '<td class="cell-num">' + fmtMetric(r.cache_read_tokens) + '</td>' +
            '<td class="cell-num">' + fmtMetric(r.cache_write_tokens) + '</td>' +
            '<td class="cell-num">' + fmtDuration(avg) + '</td>' +
            '<td class="cell-num">' + fmtMetric(perOutput) + '<span class="metric-unit"> tok/s</span></td>' +
            '</tr>';
    }}).join('');
    detail.innerHTML = sorted ? '<div class="recent-table-wrap hourly-detail-table-wrap"><table class="recent-table hourly-detail-table"><thead><tr><th>Alias</th><th>Model</th><th class="cell-num">Requests</th><th class="cell-num">Total</th><th class="cell-num">Prompt</th><th class="cell-num">Completion</th><th class="cell-num">Cache Read</th><th class="cell-num">Cache Write</th><th class="cell-num">Average Latency</th><th class="cell-num">Tokens/s</th></tr></thead><tbody>' + sorted + '</tbody></table></div>' : '<div class="hourly-detail-empty">No usage data in this period.</div>';
}}
function renderHourlyChart() {{
    var chart = document.getElementById("hourly-chart");
    var metric = "total_tokens";
    chart.innerHTML = "";
    if (!hourlyUsageData.length) {{
        chart.innerHTML = '<div class="empty-state" style="box-shadow:none;width:100%;padding:2rem;">No hourly usage yet</div>';
        document.getElementById("hourly-detail").textContent = "No hourly usage yet.";
        return;
    }}
    var maxValue = Math.max.apply(null, hourlyUsageData.map(function(item) {{ return metricValue(item, metric); }})) || 1;
    hourlyUsageData.forEach(function(item) {{
        var value = metricValue(item, metric);
        var height = Math.max(2, Math.round((value / maxValue) * 150));
        var bar = document.createElement("div");
        bar.className = "hourly-bar";
        bar.style.height = height + "px";
        Object.keys(hourlyGroups(item)).sort().forEach(function(name) {{
            var groupValue = groupMetricValue(item, name, metric);
            if (!groupValue) return;
            var segment = document.createElement("div");
            segment.className = "hourly-segment";
            segment.style.height = Math.max(2, Math.round((groupValue / value) * height)) + "px";
            segment.style.background = colorForGroup(name);
            bar.appendChild(segment);
        }});
        if (!bar.children.length) {{
            var segment = document.createElement("div");
            segment.className = "hourly-segment";
            segment.style.height = height + "px";
            segment.style.background = "#2563eb";
            bar.appendChild(segment);
        }}
        var wrap = document.createElement("div");
        wrap.className = "hourly-bar-wrap";
        wrap.innerHTML = '<div class="hourly-bar-value">' + fmtMetric(value) + '</div>';
        wrap.appendChild(bar);
        wrap.insertAdjacentHTML('beforeend', '<div class="hourly-bar-label">' + hourlyLabel(item.hour) + '</div>');
        wrap.onclick = function() {{ renderHourlyDetail(); }};
        wrap.onmousemove = function(event) {{ showTooltip(event, tooltipHtml(item, metric)); }};
        wrap.onmouseleave = hideTooltip;
        chart.appendChild(wrap);
    }});
    renderHourlyLegend();
    renderHourlyDetail();
}}
function toggleDetail(id) {{
    var el = document.getElementById(id);
    if (el.style.display === "none") {{
        el.style.display = "table-row";
    }} else {{
        el.style.display = "none";
    }}
}}
function toggleSection(id) {{
    var el = document.getElementById(id);
    var arrow = document.getElementById(id.replace("body", "arrow"));
    if (el.style.display === "none") {{
        el.style.display = "block";
        arrow.style.transform = "rotate(90deg)";
    }} else {{
        el.style.display = "none";
        arrow.style.transform = "rotate(0deg)";
    }}
}}
renderHourlyChart();
</script>

<footer>two-API &copy; 2026</footer>

</body>
</html>""".format(
        uptime_m=uptime_m, uptime_s=uptime_s,
        total_requests=total_requests, model_count=model_count,
        config_rows=config_rows,
        recent_section=recent_section, hourly_json=hourly_json,
    )
    return html


@app.get("/recent/download")
async def download_recent(request: Request, i: str = ""):
    stats = get_stats().snapshot()
    if i:
        try:
            idx = int(i)
            item = stats.get("recent", [])[idx]
            recent_json = json.dumps(item, ensure_ascii=False, indent=2)
            filename = f"request-{idx}.json"
        except (ValueError, IndexError):
            return Response(status_code=404, content="Not found")
    else:
        recent_json = json.dumps(stats.get("recent", []), ensure_ascii=False, indent=2)
        filename = "recent-requests.json"
    return Response(
        content=recent_json,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


from src.handlers.openai import router as openai_router
from src.handlers.anthropic import router as anthropic_router

app.include_router(openai_router)
app.include_router(anthropic_router)