import json
import time
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from src.forwarder import forward_non_stream, forward_stream
from src.logging_setup import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _get_router(request: Request):
    return request.app.state.router


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

    model_router = _get_router(request)
    entry = model_router.match(model_name, "anthropic")
    if entry is None:
        if model_router.match(model_name, "openai"):
            return JSONResponse(
                status_code=404,
                content={"error": f"Model '{model_name}' not available on this endpoint"},
            )
        return JSONResponse(status_code=404, content={"error": f"Unknown model: {model_name}"})

    start = time.perf_counter()
    streaming = body_json.get("stream", False)

    try:
        if streaming:
            return await forward_stream(request, entry.anthropic_base_url, entry.api_key, body=body_bytes, )
        else:
            resp = await forward_non_stream(request, entry.anthropic_base_url, entry.api_key, body=body_bytes, )
            latency_ms = int((time.perf_counter() - start) * 1000)

            input_tokens = None
            output_tokens = None
            try:
                resp_body = json.loads(resp.body)
                usage = resp_body.get("usage", {})
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
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
            )
            return resp
    except httpx.ConnectError:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})
    except httpx.TimeoutException:
        return JSONResponse(status_code=502, content={"error": "Backend unreachable"})