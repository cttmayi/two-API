import time
from .ir import Message, RequestIR, ResponseIR, StreamEventIR, Usage


def chat_request_from_ir(request: RequestIR) -> dict:
    body = {
        "model": request.model,
        "messages": [_message_from_ir(message) for message in request.messages],
    }
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.top_p is not None:
        body["top_p"] = request.top_p
    if request.stream:
        body["stream"] = True
    if request.tools:
        body["tools"] = [_chat_tool_from_ir(tool) for tool in request.tools]
    return body


def chat_response_to_ir(body: dict, model: str | None = None) -> ResponseIR:
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = body.get("usage") or {}
    return ResponseIR(
        id=_response_id(body.get("id")),
        model=model or body.get("model"),
        output_text=message.get("content") or "",
        created_at=body.get("created") or int(time.time()),
        usage=Usage(
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cache_read_tokens=usage.get("prompt_tokens_details", {}).get("cached_tokens"),
        ),
        tool_calls=message.get("tool_calls"),
    )


def chat_stream_event_to_ir(body: dict) -> StreamEventIR | None:
    choices = body.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    if delta.get("content"):
        return StreamEventIR(type="output_text_delta", delta=delta["content"])
    return None


def messages_to_dicts(messages: list[Message]) -> list[dict]:
    return [_message_from_ir(message) for message in messages]


def _message_from_ir(message: Message) -> dict:
    item = {"role": message.role, "content": message.content}
    if message.tool_calls is not None:
        item["tool_calls"] = message.tool_calls
    if message.tool_call_id is not None:
        item["tool_call_id"] = message.tool_call_id
    return item


def _chat_tool_from_ir(tool: dict) -> dict:
    if tool.get("type") == "function" and "function" not in tool:
        function = {"name": tool.get("name")}
        if "description" in tool:
            function["description"] = tool["description"]
        if "parameters" in tool:
            function["parameters"] = tool["parameters"]
        return {"type": "function", "function": function}
    return tool


def _response_id(chat_id: str | None) -> str | None:
    if chat_id and chat_id.startswith("chatcmpl"):
        return chat_id.replace("chatcmpl", "resp", 1)
    return chat_id
