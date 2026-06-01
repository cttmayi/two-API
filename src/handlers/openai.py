import json
import time
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from src.router import ModelRouter
from src.forwarder import (
    forward_non_stream, get_forward_client, _prepare_headers,
    _build_backend_url,
)
from src.logging_setup import get_logger
from src.stats import get_stats

router = APIRouter()
logger = get_logger(__name__)


def _get_router(request: Request) -> ModelRouter:
    return request.app.state.router


def _apply_alias(request: Request, body: dict) -> tuple[str, bytes | None]:
    """Apply global alias to model field. Returns (model_name, updated_body_bytes|None)."""
    model_name = body.get("model", "")
    aliased = request.app.state.config.alias.get(model_name)
    if aliased:
        body["model"] = aliased
        return aliased, json.dumps(body).encode("utf-8")
    return model_name, None


@router.post("/chat/completions")
async def chat_completions(request: Request):
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
    match_result = model_router.match(model_name, "openai")
    if match_result is None:
        if model_router.match(model_name, "anthropic"):
            return JSONResponse(
                status_code=404,
                content={"error": f"Model '{model_name}' not available on this endpoint"},
            )
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    entry, backend_model = match_result
    if backend_model != model_name:
        body_json["model"] = backend_model
        body_bytes = json.dumps(body_json).encode("utf-8")

    # Inject default max_tokens if not provided by client
    if "max_tokens" not in body_json and entry.max_tokens is not None:
        body_json["max_tokens"] = entry.max_tokens
        body_bytes = json.dumps(body_json).encode("utf-8")

    start = time.perf_counter()
    streaming = body_json.get("stream", False)

    try:
        if streaming:
            backend_url = entry.openai_base_url
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
                prompt_tokens = None
                completion_tokens = None
                cache_read_tokens = None
                # Accumulate output from deltas
                output_text = ""
                tool_calls_by_idx: dict[int, dict] = {}
                if status_code != 200:
                    output_content = "\n".join(sse_lines)
                else:
                    for line in sse_lines:
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            continue
                        try:
                            data = json.loads(data_str)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            continue
                        usage = data.get("usage", {})
                        if usage:
                            if prompt_tokens is None:
                                prompt_tokens = usage.get("prompt_tokens")
                            if completion_tokens is None:
                                completion_tokens = usage.get("completion_tokens")
                            if cache_read_tokens is None:
                                cache_read_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens")
                        choices = data.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            if delta.get("content"):
                                output_text += delta["content"]
                            for tc in delta.get("tool_calls", []):
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_by_idx:
                                    tool_calls_by_idx[idx] = {
                                        "id": tc.get("id") or "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                t = tool_calls_by_idx[idx]
                                if tc.get("id"):
                                    t["id"] = tc["id"]
                                if tc.get("function", {}).get("name"):
                                    t["function"]["name"] += tc["function"]["name"]
                                if tc.get("function", {}).get("arguments"):
                                    t["function"]["arguments"] += tc["function"]["arguments"]

                    output_content: dict = {"content": output_text}
                    if tool_calls_by_idx:
                        output_content["tool_calls"] = [tool_calls_by_idx[i] for i in sorted(tool_calls_by_idx)]

                logger.info(
                    "proxy_request",
                    model=model_name, provider="openai",
                    backend=backend_url, method=request.method,
                    path=request.url.path, latency_ms=latency_ms,
                    status=status_code,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                get_stats().record(model_name, "openai", prompt_tokens, completion_tokens,
                                   latency_ms, cache_read_tokens=cache_read_tokens)
                get_stats().record_detail(
                    model=model_name, provider="openai", streaming=True,
                    latency_ms=latency_ms, status=status_code,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    cache_read=cache_read_tokens, cache_write=None,
                    input_messages=body_json.get("messages", []),
                    output_content=output_content,
                )

            return StreamingResponse(stream_with_stats(), media_type="text/event-stream")
        else:
            resp = await forward_non_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes, )
            latency_ms = int((time.perf_counter() - start) * 1000)

            prompt_tokens = None
            completion_tokens = None
            cache_read_tokens = None
            output_content = None
            try:
                resp_body = json.loads(resp.body)
                usage = resp_body.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens")
                completion_tokens = usage.get("completion_tokens")
                cache_read_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens")
                output_content = resp_body.get("choices", [{}])[0].get("message")
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

            if resp.status_code != 200:
                output_content = resp.body.decode("utf-8", errors="replace")

            logger.info(
                "proxy_request",
                model=model_name,
                provider="openai",
                backend=entry.openai_base_url,
                method=request.method,
                path=request.url.path,
                latency_ms=latency_ms,
                status=resp.status_code,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            get_stats().record(model_name, "openai", prompt_tokens, completion_tokens, latency_ms,
                               cache_read_tokens=cache_read_tokens)
            get_stats().record_detail(
                model=model_name, provider="openai", streaming=False,
                latency_ms=latency_ms, status=resp.status_code,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                cache_read=cache_read_tokens, cache_write=None,
                input_messages=body_json.get("messages", []),
                output_content=output_content,
            )
            return resp
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
    except httpx.TimeoutException:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})


@router.get("/v1/models")
@router.get("/models")
async def list_models(request: Request):
    model_router = _get_router(request)
    models = model_router.list_models("openai")
    return JSONResponse(content={
        "object": "list",
        "data": [{"id": name, "object": "model"} for name in models],
    })


@router.post("/embeddings")
async def embeddings(request: Request):
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
    match_result = model_router.match(model_name, "openai")
    if match_result is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    entry, backend_model = match_result
    if backend_model != model_name:
        body_json["model"] = backend_model
        body_bytes = json.dumps(body_json).encode("utf-8")

    # Inject default max_tokens if not provided by client
    if "max_tokens" not in body_json and entry.max_tokens is not None:
        body_json["max_tokens"] = entry.max_tokens
        body_bytes = json.dumps(body_json).encode("utf-8")

    resp = await forward_non_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes, )
    return resp