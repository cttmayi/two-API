import json
import time
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from src.forwarder import (
    forward_non_stream, get_forward_client, _prepare_headers,
    _build_backend_url,
)
from src.logging_setup import get_logger
from src.stats import get_stats

router = APIRouter()
logger = get_logger(__name__)


def _get_router(request: Request):
    return request.app.state.router


def _apply_alias(request: Request, body: dict) -> tuple[str, bytes | None]:
    model_name = body.get("model", "")
    aliased = request.app.state.config.alias.get(model_name)
    if aliased:
        body["model"] = aliased
        return aliased, json.dumps(body).encode("utf-8")
    return model_name, None


@router.post("/v1/messages")
@router.post("/messages")
async def messages(request: Request):
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    model_name = body_json.get("model")
    if not model_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'model' field"})

    model_name, new_body = _apply_alias(request, body_json)
    if new_body:
        body_bytes = new_body

    model_router = _get_router(request)
    match_result = model_router.match(model_name, "anthropic")
    if match_result is None:
        if model_router.match(model_name, "openai"):
            return JSONResponse(
                status_code=404,
                content={"error": f"Model '{model_name}' not available on this endpoint"},
            )
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    entry, backend_model = match_result
    if backend_model != model_name:
        body_json["model"] = backend_model
        body_bytes = json.dumps(body_json).encode("utf-8")

    start = time.perf_counter()
    streaming = body_json.get("stream", False)

    try:
        if streaming:
            backend_url = entry.anthropic_base_url
            api_key = entry.api_key

            async def stream_with_stats():
                url = _build_backend_url(backend_url, request.url.path, request.url.query)
                req_headers = _prepare_headers(dict(request.headers), api_key)
                client = get_forward_client()
                status_code = 200
                sse_lines: list[str] = []
                partial = ""
                async with client.stream(
                    method=request.method, url=url, headers=req_headers,
                    content=body_bytes,
                ) as resp:
                    status_code = resp.status_code
                    async for chunk in resp.aiter_bytes():
                        yield chunk
                        text = chunk.decode("utf-8", errors="replace")
                        partial += text
                        while "\n" in partial:
                            line, partial = partial.split("\n", 1)
                            line = line.rstrip("\r")
                            if line:
                                sse_lines.append(line)

                latency_ms = int((time.perf_counter() - start) * 1000)
                input_tokens = None
                output_tokens = None
                cache_read_tokens = None
                cache_write_tokens = None
                for line in reversed(sse_lines):
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                        try:
                            data = json.loads(data_str)
                            usage = data.get("usage", {})
                            if usage:
                                if input_tokens is None:
                                    input_tokens = usage.get("input_tokens")
                                if output_tokens is None:
                                    output_tokens = usage.get("output_tokens")
                                if cache_read_tokens is None:
                                    cache_read_tokens = usage.get("cache_read_input_tokens")
                                if cache_write_tokens is None:
                                    cache_write_tokens = usage.get("cache_creation_input_tokens")
                                if all(v is not None for v in [input_tokens, output_tokens, cache_read_tokens, cache_write_tokens]):
                                    break
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass

                logger.info(
                    "proxy_request",
                    model=model_name, provider="anthropic",
                    backend=backend_url, method=request.method,
                    path=request.url.path, latency_ms=latency_ms,
                    status=status_code,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                )
                get_stats().record(model_name, "anthropic", input_tokens, output_tokens,
                                   latency_ms, cache_read_tokens=cache_read_tokens,
                                   cache_write_tokens=cache_write_tokens)

            return StreamingResponse(stream_with_stats(), media_type="text/event-stream")
        else:
            resp = await forward_non_stream(request, entry.anthropic_base_url, entry.api_key, body=body_bytes, )
            latency_ms = int((time.perf_counter() - start) * 1000)

            input_tokens = None
            output_tokens = None
            cache_read_tokens = None
            cache_write_tokens = None
            try:
                resp_body = json.loads(resp.body)
                usage = resp_body.get("usage", {})
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                cache_read_tokens = usage.get("cache_read_input_tokens")
                cache_write_tokens = usage.get("cache_creation_input_tokens")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            logger.info(
                "proxy_request",
                model=model_name,
                provider="anthropic",
                backend=entry.anthropic_base_url,
                method=request.method,
                path=request.url.path,
                latency_ms=latency_ms,
                status=resp.status_code,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            )
            get_stats().record(model_name, "anthropic", input_tokens, output_tokens, latency_ms,
                               cache_read_tokens=cache_read_tokens,
                               cache_write_tokens=cache_write_tokens)
            return resp
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
    except httpx.TimeoutException:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})