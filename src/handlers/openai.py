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
from src.transforms.ir import StreamEventIR
from src.transforms.openai_chat import chat_request_from_ir, chat_response_to_ir, chat_stream_event_to_ir, messages_to_dicts
from src.transforms.openai_responses import responses_request_to_ir, responses_response_from_ir, responses_stream_event_from_ir

router = APIRouter()
logger = get_logger(__name__)


def _get_router(request: Request) -> ModelRouter:
    return request.app.state.router


def _dump_body(body: dict) -> bytes:
    return json.dumps(body).encode("utf-8")


def _apply_alias(request: Request, body: dict) -> tuple[str, str]:
    model_name = body.get("model", "")
    aliased = request.app.state.config.alias.get(model_name)
    if aliased:
        body["model"] = aliased
        return model_name, aliased
    return "", model_name


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
    alias_name, model_name = _apply_alias(request, body_json)
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

    return body_bytes, body_json, model_name, backend_model, alias_name, entry, original_body


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


def _cache_read_tokens_from_usage(usage: dict) -> int | None:
    return (
        usage.get("prompt_tokens_details", {}).get("cached_tokens")
        or usage.get("input_tokens_details", {}).get("cached_tokens")
        or usage.get("prompt_cache_hit_tokens")
        or usage.get("prompt_cache_read_tokens")
        or usage.get("cache_read_input_tokens")
        or usage.get("cache_creation_input_tokens")
        or usage.get("cached_tokens")
    )


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
                cache_read_tokens = _cache_read_tokens_from_usage(usage)
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


def _unsupported_responses_to_chat_field(body: dict) -> str | None:
    if "previous_response_id" in body:
        return "previous_response_id"
    return None


def _responses_stream_stats(sse_lines: list[str], status_code: int):
    input_tokens = None
    output_tokens = None
    cache_read_tokens = None
    output_text = ""
    output_content = {"output_text": output_text}

    if status_code != 200:
        return None, None, None, "\n".join(sse_lines)

    for data in _iter_sse_json(sse_lines):
        if data.get("type") == "response.output_text.delta" and data.get("delta"):
            output_text += data["delta"]
            output_content["output_text"] = output_text
        response = data.get("response", {})
        usage = data.get("usage") or response.get("usage", {})
        if usage:
            output_content["usage"] = usage
            if input_tokens is None:
                input_tokens = usage.get("input_tokens")
            if output_tokens is None:
                output_tokens = usage.get("output_tokens")
            if cache_read_tokens is None:
                cache_read_tokens = _cache_read_tokens_from_usage(usage)

    return input_tokens, output_tokens, cache_read_tokens, output_content


def _record_openai_request(
    *,
    model_name: str,
    alias_name: str,
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
    request_body=None,
):
    logger.info(
        "proxy_request",
        model=model_name,
        alias=alias_name,
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
        alias=alias_name,
    )
    get_stats().record_detail(
        model=model_name,
        alias=alias_name,
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
        request_body=request_body,
    )


def _sse_event(event: str, data: dict) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _responses_function_call_item(tool_call: dict) -> dict:
    function = tool_call.get("function", {})
    return {
        "id": tool_call.get("id"),
        "type": "function_call",
        "call_id": tool_call.get("id"),
        "name": function.get("name"),
        "arguments": function.get("arguments", ""),
        "status": "completed",
    }


def _responses_message_item(message_id: str, status: str, output_text: str = "") -> dict:
    return {
        "id": message_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": output_text, "annotations": []}] if output_text else [],
    }


def _streaming_proxy_response(
    request: Request,
    *,
    body_bytes: bytes,
    body_json: dict,
    entry,
    model_name: str,
    stats_model_name: str,
    alias_name: str,
    start: float,
    input_messages,
    parse_stats,
    request_body=None,
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
            model_name=stats_model_name,
            alias_name=alias_name,
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
            request_body=request_body,
        )

    return StreamingResponse(stream_with_stats(), media_type="text/event-stream")


def _responses_to_chat_streaming_response(
    request: Request,
    *,
    chat_body: dict,
    entry,
    model_name: str,
    stats_model_name: str,
    alias_name: str,
    start: float,
    request_body=None,
):
    async def stream_converted():
        chat_body_bytes = _dump_body(chat_body)
        chat_request = Request(request.scope, request.receive)
        chat_request.scope["path"] = "/chat/completions"
        chat_request.scope["raw_path"] = b"/chat/completions"
        url = _build_backend_url(entry.openai_base_url, chat_request.url.path, chat_request.url.query)
        req_headers = _prepare_headers(dict(request.headers), entry.api_key)
        client = get_forward_client()
        status_code = 200
        sse_lines: list[str] = []
        partial = ""
        output_text = ""
        tool_calls_by_idx: dict[int, dict] = {}
        emitted_tool_call_idxs: set[int] = set()
        response_id = "resp_chatcmpl"
        message_id = "msg_resp_chatcmpl"
        text_output_started = False
        created_event = responses_stream_event_from_ir(StreamEventIR(type="response_created", response={"id": response_id, "object": "response", "status": "in_progress", "model": model_name}))
        if created_event:
            yield _sse_event(*created_event)
        async with client.stream(method=request.method, url=url, headers=req_headers, content=chat_body_bytes) as resp:
            status_code = resp.status_code
            async for chunk in resp.aiter_bytes():
                partial += chunk.decode("utf-8", errors="replace")
                while "\n" in partial:
                    line, partial = partial.split("\n", 1)
                    line = line.rstrip("\r")
                    if not line:
                        continue
                    sse_lines.append(line)
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        continue
                    try:
                        data = json.loads(data_str)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if data.get("id"):
                        response_id = data["id"].replace("chatcmpl", "resp", 1) if data["id"].startswith("chatcmpl") else data["id"]
                        message_id = f"msg_{response_id}"
                    choices = data.get("choices") or []
                    finish_reason = choices[0].get("finish_reason") if choices else None
                    delta = choices[0].get("delta") if choices else {}
                    for tool_call in (delta or {}).get("tool_calls", []):
                        idx = tool_call.get("index", 0)
                        if idx not in tool_calls_by_idx:
                            tool_calls_by_idx[idx] = {
                                "id": tool_call.get("id") or f"call_{idx}",
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
                    if finish_reason == "tool_calls":
                        for idx in sorted(tool_calls_by_idx):
                            if idx in emitted_tool_call_idxs:
                                continue
                            emitted_tool_call_idxs.add(idx)
                            yield _sse_event("response.output_item.done", {"type": "response.output_item.done", "item": _responses_function_call_item(tool_calls_by_idx[idx])})
                    event_ir = chat_stream_event_to_ir(data)
                    event = responses_stream_event_from_ir(event_ir) if event_ir else None
                    if event and event_ir and event_ir.delta:
                        if not text_output_started:
                            text_output_started = True
                            yield _sse_event("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": _responses_message_item(message_id, "in_progress")})
                            yield _sse_event("response.content_part.added", {"type": "response.content_part.added", "item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})
                        output_text += event_ir.delta
                        yield _sse_event(*event)
        prompt_tokens, completion_tokens, cache_read_tokens, output_content = _chat_stream_stats(sse_lines, status_code)
        if status_code != 200 and not output_content:
            output_content = {
                "backend_status": status_code,
                "backend_error": "empty response body",
                "converted_request": chat_body,
            }
        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = {
            "input_tokens": prompt_tokens or 0,
            "output_tokens": completion_tokens or 0,
        }
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        if cache_read_tokens:
            usage["input_tokens_details"] = {"cached_tokens": cache_read_tokens}
        output = []
        if text_output_started:
            yield _sse_event("response.output_text.done", {"type": "response.output_text.done", "item_id": message_id, "output_index": 0, "content_index": 0, "text": output_text})
            yield _sse_event("response.content_part.done", {"type": "response.content_part.done", "item_id": message_id, "output_index": 0, "content_index": 0, "part": {"type": "output_text", "text": output_text, "annotations": []}})
            message_item = _responses_message_item(message_id, "completed", output_text)
            output.append(message_item)
            yield _sse_event("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": message_item})
        completed_response = {"id": response_id, "object": "response", "status": "completed", "model": model_name, "output": output, "output_text": output_text, "usage": usage}
        detail_output = dict(completed_response)
        detail_output["converted_request"] = chat_body
        _record_openai_request(
            model_name=stats_model_name,
            alias_name=alias_name,
            backend_url=entry.openai_base_url,
            method=request.method,
            path=request.url.path,
            latency_ms=latency_ms,
            status_code=status_code,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            streaming=True,
            input_messages=chat_body.get("messages", []),
            output_content=detail_output if status_code == 200 else output_content,
            request_body=request_body,
        )
        completed_event = responses_stream_event_from_ir(StreamEventIR(type="response_completed", response=completed_response))
        if completed_event:
            yield _sse_event(*completed_event)

    return StreamingResponse(stream_converted(), media_type="text/event-stream")


@router.post("/chat/completions")
async def chat_completions(request: Request):
    prepared = await _prepare_openai_request(request, "max_tokens")
    if isinstance(prepared, JSONResponse):
        return prepared
    body_bytes, body_json, model_name, backend_model, alias_name, entry, original_body = prepared
    start = time.perf_counter()

    try:
        if body_json.get("stream", False):
            return _streaming_proxy_response(
                request,
                body_bytes=body_bytes,
                body_json=body_json,
                entry=entry,
                model_name=model_name,
                stats_model_name=backend_model,
                alias_name=alias_name,
                start=start,
                input_messages=lambda body: body.get("messages", []),
                parse_stats=_chat_stream_stats,
                request_body=original_body,
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
            cache_read_tokens = _cache_read_tokens_from_usage(usage)
            output_content = resp_body.get("choices", [{}])[0].get("message")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if resp.status_code != 200:
            output_content = resp.body.decode("utf-8", errors="replace")

        _record_openai_request(
            model_name=backend_model,
            alias_name=alias_name,
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
            request_body=original_body,
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
    body_bytes, body_json, model_name, backend_model, alias_name, entry, original_body = prepared
    start = time.perf_counter()

    try:
        if entry.responses_to_chat:
            unsupported_field = _unsupported_responses_to_chat_field(body_json)
            if unsupported_field:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"Field {unsupported_field} is not supported when responses_to_chat is enabled"},
                )
            request_ir = responses_request_to_ir(body_json)
            chat_body = chat_request_from_ir(request_ir)
            if body_json.get("stream", False):
                return _responses_to_chat_streaming_response(
                    request,
                    chat_body=chat_body,
                    entry=entry,
                    model_name=model_name,
                    stats_model_name=backend_model,
                    alias_name=alias_name,
                    start=start,
                    request_body=original_body,
                )
            chat_body_bytes = _dump_body(chat_body)
            chat_request = Request(request.scope, request.receive)
            chat_request.scope["path"] = "/chat/completions"
            chat_request.scope["raw_path"] = b"/chat/completions"
            resp = await forward_non_stream(chat_request, entry.openai_base_url, entry.api_key, body=chat_body_bytes)
            latency_ms = int((time.perf_counter() - start) * 1000)

            input_tokens = None
            output_tokens = None
            cache_read_tokens = None
            output_content = None
            if resp.status_code == 200:
                try:
                    chat_resp_body = json.loads(resp.body)
                    converted_body = responses_response_from_ir(chat_response_to_ir(chat_resp_body, model=model_name))
                    usage = converted_body.get("usage", {})
                    input_tokens = usage.get("input_tokens")
                    output_tokens = usage.get("output_tokens")
                    cache_read_tokens = usage.get("input_tokens_details", {}).get("cached_tokens")
                    output_content = dict(converted_body)
                    output_content["converted_request"] = chat_body
                    resp = JSONResponse(status_code=200, content=converted_body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    output_content = resp.body.decode("utf-8", errors="replace")
                    resp = JSONResponse(status_code=502, content={"error": "Unable to convert chat response"})
            else:
                error_body = resp.body.decode("utf-8", errors="replace")
                output_content = error_body or {
                    "backend_status": resp.status_code,
                    "backend_error": "empty response body",
                    "converted_request": chat_body,
                }

            _record_openai_request(
                model_name=backend_model,
                alias_name=alias_name,
                backend_url=entry.openai_base_url,
                method=request.method,
                path=request.url.path,
                latency_ms=latency_ms,
                status_code=resp.status_code,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                streaming=False,
                input_messages=chat_body.get("messages", []),
                output_content=output_content,
                request_body=original_body,
            )
            return resp

        if body_json.get("stream", False):
            return _streaming_proxy_response(
                request,
                body_bytes=body_bytes,
                body_json=body_json,
                entry=entry,
                model_name=model_name,
                stats_model_name=backend_model,
                alias_name=alias_name,
                start=start,
                input_messages=lambda body: messages_to_dicts(responses_request_to_ir(body).messages),
                parse_stats=_responses_stream_stats,
                request_body=original_body,
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
            cache_read_tokens = _cache_read_tokens_from_usage(usage)
            output_content = resp_body
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if resp.status_code != 200:
            output_content = resp.body.decode("utf-8", errors="replace")

        _record_openai_request(
            model_name=backend_model,
            alias_name=alias_name,
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
            request_body=original_body,
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

    _, model_name = _apply_alias(request, body_json)
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
