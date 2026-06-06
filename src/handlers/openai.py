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


def _dump_body(body: dict) -> bytes:
    return json.dumps(body).encode("utf-8")


def _apply_alias(request: Request, body: dict) -> str:
    model_name = body.get("model", "")
    aliased = request.app.state.config.alias.get(model_name)
    if aliased:
        body["model"] = aliased
        return aliased
    return model_name


async def _prepare_openai_request(request: Request, default_token_field: str):
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(status_code=400, content={"error": "Invalid JSON body"})

    model_name = body_json.get("model")
    if not model_name:
        return JSONResponse(status_code=400, content={"error": "Missing 'model' field"})

    original_body = dict(body_json)
    model_name = _apply_alias(request, body_json)
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
    if default_token_field not in body_json and entry.max_tokens is not None:
        body_json[default_token_field] = entry.max_tokens

    if body_json != original_body:
        body_bytes = _dump_body(body_json)

    return body_bytes, body_json, model_name, entry


def _iter_sse_json(sse_lines: list[str]):
    for line in sse_lines:
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            continue
        try:
            yield json.loads(data_str)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue


def _chat_stream_stats(sse_lines: list[str], status_code: int):
    prompt_tokens = None
    completion_tokens = None
    cache_read_tokens = None
    output_text = ""
    tool_calls_by_idx: dict[int, dict] = {}

    if status_code != 200:
        return None, None, None, "\n".join(sse_lines)

    for data in _iter_sse_json(sse_lines):
        usage = data.get("usage", {})
        if usage:
            if prompt_tokens is None:
                prompt_tokens = usage.get("prompt_tokens")
            if completion_tokens is None:
                completion_tokens = usage.get("completion_tokens")
            if cache_read_tokens is None:
                cache_read_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens")
        choices = data.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        if delta.get("content"):
            output_text += delta["content"]
        for tool_call in delta.get("tool_calls", []):
            idx = tool_call.get("index", 0)
            if idx not in tool_calls_by_idx:
                tool_calls_by_idx[idx] = {
                    "id": tool_call.get("id") or "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                }
            existing = tool_calls_by_idx[idx]
            if tool_call.get("id"):
                existing["id"] = tool_call["id"]
            function = tool_call.get("function", {})
            if function.get("name"):
                existing["function"]["name"] += function["name"]
            if function.get("arguments"):
                existing["function"]["arguments"] += function["arguments"]

    output_content: dict = {"content": output_text}
    if tool_calls_by_idx:
        output_content["tool_calls"] = [tool_calls_by_idx[i] for i in sorted(tool_calls_by_idx)]
    return prompt_tokens, completion_tokens, cache_read_tokens, output_content


def _responses_stream_stats(sse_lines: list[str], status_code: int):
    input_tokens = None
    output_tokens = None
    cache_read_tokens = None
    output_text = ""

    if status_code != 200:
        return None, None, None, "\n".join(sse_lines)

    for data in _iter_sse_json(sse_lines):
        if data.get("type") == "response.output_text.delta" and data.get("delta"):
            output_text += data["delta"]
        response = data.get("response", {})
        usage = data.get("usage") or response.get("usage", {})
        if usage:
            if input_tokens is None:
                input_tokens = usage.get("input_tokens")
            if output_tokens is None:
                output_tokens = usage.get("output_tokens")
            if cache_read_tokens is None:
                cache_read_tokens = usage.get("input_tokens_details", {}).get("cached_tokens")

    return input_tokens, output_tokens, cache_read_tokens, {"output_text": output_text}


def _record_openai_request(
    *,
    model_name: str,
    backend_url: str,
    method: str,
    path: str,
    latency_ms: int,
    status_code: int,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cache_read_tokens: int | None,
    streaming: bool,
    input_messages,
    output_content,
):
    logger.info(
        "proxy_request",
        model=model_name,
        provider="openai",
        backend=backend_url,
        method=method,
        path=path,
        latency_ms=latency_ms,
        status=status_code,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    get_stats().record(
        model_name, "openai", prompt_tokens, completion_tokens, latency_ms,
        cache_read_tokens=cache_read_tokens,
    )
    get_stats().record_detail(
        model=model_name,
        provider="openai",
        streaming=streaming,
        latency_ms=latency_ms,
        status=status_code,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read=cache_read_tokens,
        cache_write=None,
        input_messages=input_messages,
        output_content=output_content,
    )


def _streaming_proxy_response(
    request: Request,
    *,
    body_bytes: bytes,
    body_json: dict,
    entry,
    model_name: str,
    start: float,
    input_messages,
    parse_stats,
):
    async def stream_with_stats():
        url = _build_backend_url(entry.openai_base_url, request.url.path, request.url.query)
        req_headers = _prepare_headers(dict(request.headers), entry.api_key)
        client = get_forward_client()
        status_code = 200
        sse_lines: list[str] = []
        partial = ""
        async with client.stream(
            method=request.method, url=url, headers=req_headers, content=body_bytes,
        ) as resp:
            status_code = resp.status_code
            async for chunk in resp.aiter_bytes():
                yield chunk
                partial += chunk.decode("utf-8", errors="replace")
                while "\n" in partial:
                    line, partial = partial.split("\n", 1)
                    line = line.rstrip("\r")
                    if line:
                        sse_lines.append(line)

        latency_ms = int((time.perf_counter() - start) * 1000)
        prompt_tokens, completion_tokens, cache_read_tokens, output_content = parse_stats(sse_lines, status_code)
        _record_openai_request(
            model_name=model_name,
            backend_url=entry.openai_base_url,
            method=request.method,
            path=request.url.path,
            latency_ms=latency_ms,
            status_code=status_code,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            streaming=True,
            input_messages=input_messages(body_json),
            output_content=output_content,
        )

    return StreamingResponse(stream_with_stats(), media_type="text/event-stream")


@router.post("/chat/completions")
async def chat_completions(request: Request):
    prepared = await _prepare_openai_request(request, "max_tokens")
    if isinstance(prepared, JSONResponse):
        return prepared
    body_bytes, body_json, model_name, entry = prepared
    start = time.perf_counter()

    try:
        if body_json.get("stream", False):
            return _streaming_proxy_response(
                request,
                body_bytes=body_bytes,
                body_json=body_json,
                entry=entry,
                model_name=model_name,
                start=start,
                input_messages=lambda body: body.get("messages", []),
                parse_stats=_chat_stream_stats,
            )

        resp = await forward_non_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes)
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

        _record_openai_request(
            model_name=model_name,
            backend_url=entry.openai_base_url,
            method=request.method,
            path=request.url.path,
            latency_ms=latency_ms,
            status_code=resp.status_code,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            streaming=False,
            input_messages=body_json.get("messages", []),
            output_content=output_content,
        )
        return resp
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
    except httpx.TimeoutException:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})


@router.post("/v1/responses")
@router.post("/responses")
async def responses(request: Request):
    prepared = await _prepare_openai_request(request, "max_output_tokens")
    if isinstance(prepared, JSONResponse):
        return prepared
    body_bytes, body_json, model_name, entry = prepared
    start = time.perf_counter()

    try:
        if body_json.get("stream", False):
            return _streaming_proxy_response(
                request,
                body_bytes=body_bytes,
                body_json=body_json,
                entry=entry,
                model_name=model_name,
                start=start,
                input_messages=lambda body: [{"role": "user", "content": body.get("input", "")}],
                parse_stats=_responses_stream_stats,
            )

        resp = await forward_non_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes)
        latency_ms = int((time.perf_counter() - start) * 1000)

        input_tokens = None
        output_tokens = None
        cache_read_tokens = None
        output_content = None
        try:
            resp_body = json.loads(resp.body)
            usage = resp_body.get("usage", {})
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            cache_read_tokens = usage.get("input_tokens_details", {}).get("cached_tokens")
            output_content = resp_body.get("output_text") or resp_body.get("output")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if resp.status_code != 200:
            output_content = resp.body.decode("utf-8", errors="replace")

        _record_openai_request(
            model_name=model_name,
            backend_url=entry.openai_base_url,
            method=request.method,
            path=request.url.path,
            latency_ms=latency_ms,
            status_code=resp.status_code,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            streaming=False,
            input_messages=[{"role": "user", "content": body_json.get("input", "")}],
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

    model_name = _apply_alias(request, body_json)
    body_bytes = _dump_body(body_json)

    model_router = _get_router(request)
    match_result = model_router.match(model_name, "openai")
    if match_result is None:
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    entry, backend_model = match_result
    if backend_model != model_name:
        body_json["model"] = backend_model
        body_bytes = _dump_body(body_json)

    if "max_tokens" not in body_json and entry.max_tokens is not None:
        body_json["max_tokens"] = entry.max_tokens
        body_bytes = _dump_body(body_json)

    resp = await forward_non_stream(request, entry.openai_base_url, entry.api_key, body=body_bytes)
    return resp
