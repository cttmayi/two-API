from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from src.config import load_config
from src.router import ModelRouter
from src.logging_setup import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    config = load_config("config.yaml")
    app.state.config = config
    app.state.router = ModelRouter(config.models)
    log_path = setup_logging(config.logging.dir, config.logging.level)
    app.state.log_path = log_path
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


@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    config = request.app.state.config
    stats = __import__("src.stats", fromlist=["get_stats"]).get_stats().snapshot()

    config_rows = ""
    for entry in config.models:
        backends = []
        if entry.openai_base_url:
            backends.append(f'<span class="proto-tag">OpenAI</span> {entry.openai_base_url}')
        if entry.anthropic_base_url:
            backends.append(f'<span class="proto-tag">Anthropic</span> {entry.anthropic_base_url}')
        backends_html = "<br>".join(backends) if backends else "&mdash;"
        has_key = '<span class="tag tag-yes">Yes</span>' if entry.api_key else '<span class="tag tag-no">No</span>'
        for client_name, backend_name in entry.get_name_map().items():
            same = "same" if client_name == backend_name else ""
            config_rows += f"""
        <tr>
            <td class="model-name-cell">{client_name}</td>
            <td class="backend-name-cell {same}">{backend_name}</td>
            <td class="backend-cell">{backends_html}</td>
            <td class="key-cell">{has_key}</td>
        </tr>"""

    stats_cards = ""
    for name, m in stats.get("models", {}).items():
        avg_lat = m["total_latency_ms"] / m["requests"] if m["requests"] else 0
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
            </div>
        </div>"""

    uptime_m = stats["uptime_seconds"] // 60
    uptime_s = stats["uptime_seconds"] % 60

    html = f"""<!DOCTYPE html>
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
    grid-template-columns: repeat(6, 1fr);
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

.empty-state {{
    text-align: center;
    padding: 3rem;
    color: #bbb;
    background: #fff;
    border-radius: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
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
        <div class="value">{stats["total_requests"]}</div>
        <div class="label">Total Requests</div>
    </div>
    <div class="summary-card">
        <div class="value">{len(config.models)}</div>
        <div class="label">Model Groups</div>
    </div>
</div>

<div class="section">
    <div class="section-title">Model Configuration</div>
    <table class="config-table">
    <thead><tr><th>Model Name</th><th>Backend Name</th><th>Backend URLs</th><th>API Key</th></tr></thead>
    <tbody>{config_rows}</tbody>
    </table>
</div>

<div class="section">
    <div class="section-title">Usage Statistics</div>
    <div class="model-cards">
        {stats_cards if stats_cards else '<div class="empty-state">No requests processed yet</div>'}
    </div>
</div>

<footer>two-API &copy; 2026</footer>

</body>
</html>"""
    return html


from src.handlers.openai import router as openai_router
from src.handlers.anthropic import router as anthropic_router

app.include_router(openai_router)
app.include_router(anthropic_router)