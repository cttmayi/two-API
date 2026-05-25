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


@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    config = request.app.state.config
    stats = __import__("src.stats", fromlist=["get_stats"]).get_stats().snapshot()

    models_html = ""
    for entry in config.models:
        names = ", ".join(entry.names)
        backends = []
        if entry.openai_base_url:
            backends.append(f"OpenAI: {entry.openai_base_url}")
        if entry.anthropic_base_url:
            backends.append(f"Anthropic: {entry.anthropic_base_url}")
        backend_str = "<br>".join(backends)
        has_key = "Yes" if entry.api_key else "No"
        models_html += f"""
        <tr>
            <td>{names}</td>
            <td>{backend_str}</td>
            <td>{has_key}</td>
        </tr>"""

    stats_rows = ""
    for name, m in stats.get("models", {}).items():
        avg_lat = m["total_latency_ms"] / m["requests"] if m["requests"] else 0
        stats_rows += f"""
        <tr>
            <td>{name}</td>
            <td>{m["provider"]}</td>
            <td>{m["requests"]}</td>
            <td>{m["prompt_tokens"]}</td>
            <td>{m["completion_tokens"]}</td>
            <td>{m["cache_read_tokens"]}</td>
            <td>{m["cache_write_tokens"]}</td>
            <td>{avg_lat:.0f}</td>
        </tr>"""

    uptime_m = stats["uptime_seconds"] // 60
    uptime_s = stats["uptime_seconds"] % 60

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>two-API Proxy</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 960px; margin: 0 auto; padding: 2rem; background: #f5f5f5; }}
h1 {{ color: #333; }}
h2 {{ color: #555; margin-top: 2rem; }}
table {{ width: 100%; border-collapse: collapse; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th, td {{ padding: 10px 14px; text-align: left; border-bottom: 1px solid #eee; }}
th {{ background: #fafafa; font-weight: 600; }}
tr:hover {{ background: #f9f9f9; }}
.stat {{ display: inline-block; margin: 0 2rem 1rem 0; }}
.stat-value {{ font-size: 1.5rem; font-weight: bold; color: #2563eb; }}
.stat-label {{ font-size: 0.85rem; color: #888; }}
</style>
</head>
<body>
<h1>two-API Proxy</h1>

<div>
    <div class="stat"><div class="stat-value">{uptime_m}m {uptime_s}s</div><div class="stat-label">Uptime</div></div>
    <div class="stat"><div class="stat-value">{stats["total_requests"]}</div><div class="stat-label">Total Requests</div></div>
    <div class="stat"><div class="stat-value">{len(config.models)}</div><div class="stat-label">Model Groups</div></div>
</div>

<h2>Config — Models</h2>
<table>
<tr><th>Names</th><th>Backends</th><th>API Key</th></tr>
{models_html}
</table>

<h2>Token / Usage Stats</h2>
<table>
<tr><th>Model</th><th>Provider</th><th>Requests</th><th>Prompt Tokens</th><th>Completion Tokens</th><th>Cache Read</th><th>Cache Write</th><th>Avg Latency (ms)</th></tr>
{stats_rows if stats_rows else '<tr><td colspan="8" style="color:#999;">No requests yet</td></tr>'}
</table>
</body>
</html>"""
    return html


from src.handlers.openai import router as openai_router
from src.handlers.anthropic import router as anthropic_router

app.include_router(openai_router)
app.include_router(anthropic_router)