import httpx
from fastapi import Request
from fastapi.responses import Response, StreamingResponse

HOP_BY_HOP_HEADERS = {
    "host",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
    "content-length",
    "content-encoding",
}

_forward_client: httpx.AsyncClient | None = None


def get_forward_client() -> httpx.AsyncClient:
    global _forward_client
    if _forward_client is None:
        _forward_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
    return _forward_client


def set_forward_client(client: httpx.AsyncClient):
    global _forward_client
    _forward_client = client


def reset_forward_client():
    global _forward_client
    _forward_client = None


def _prepare_headers(headers: dict, api_key: str | None) -> dict:
    result = {}
    for key, value in headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS:
            result[key] = value
    if api_key:
        result["authorization"] = f"Bearer {api_key}"
    return result


def _build_backend_url(base_url: str, path: str, query_string: str) -> str:
    url = base_url.rstrip("/") + path
    if query_string:
        url += "?" + query_string
    return url


async def forward_non_stream(
    request: Request,
    base_url: str,
    api_key: str | None,
    body: bytes | None = None,
) -> Response:
    url = _build_backend_url(base_url, request.url.path, request.url.query)
    headers = _prepare_headers(dict(request.headers), api_key)
    if body is None:
        body = await request.body()

    client = get_forward_client()
    resp = await client.request(
        method=request.method,
        url=url,
        headers=headers,
        content=body,
    )

    response_headers = {}
    for key, value in resp.headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS:
            response_headers[key] = value

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=response_headers,
    )


async def forward_stream(
    request: Request,
    base_url: str,
    api_key: str | None,
    body: bytes | None = None,
) -> StreamingResponse:
    url = _build_backend_url(base_url, request.url.path, request.url.query)
    headers = _prepare_headers(dict(request.headers), api_key)
    if body is None:
        body = await request.body()

    client = get_forward_client()

    async def stream_bytes():
        async with client.stream(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
        ) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk

    return StreamingResponse(stream_bytes(), media_type="text/event-stream")