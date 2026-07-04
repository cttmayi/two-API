import json

from .ir import Message, RequestIR, ResponseIR, StreamEventIR


def responses_request_to_ir(body: dict) -> RequestIR:
    messages = []
    if body.get("instructions"):
        messages.append(Message(role="system", content=body["instructions"]))
    input_value = body.get("input", "")
    if isinstance(input_value, str):
        messages.append(Message(role="user", content=input_value))
    elif isinstance(input_value, list):
        for item in input_value:
            if item.get("type") == "function_call":
                tool_call = _chat_tool_call(item)
                if messages and messages[-1].role == "assistant" and messages[-1].tool_calls is not None:
                    messages[-1].tool_calls.append(tool_call)
                else:
                    messages.append(Message(role="assistant", content="", tool_calls=[tool_call]))
                continue
            if item.get("type") == "function_call_output":
                messages.append(Message(role="tool", content=item.get("output") or "", tool_call_id=item.get("call_id")))
                continue
            role = _chat_role(item.get("role", "user"))
            content = item.get("content", "")
            tool_calls = item.get("tool_calls")
            if isinstance(content, list):
                extracted_tool_calls, content = _extract_tool_calls(content)
                if extracted_tool_calls:
                    tool_calls = (tool_calls or []) + extracted_tool_calls
                content = _flatten_content_blocks(content)
            if _is_empty_content(content) and not tool_calls:
                continue
            messages.append(Message(role=role, content=content, tool_calls=tool_calls))
    else:
        messages.append(Message(role="user", content=input_value))
    return RequestIR(
        model=body["model"],
        messages=messages,
        max_output_tokens=body.get("max_output_tokens"),
        temperature=body.get("temperature"),
        top_p=body.get("top_p"),
        stream=body.get("stream", False),
        tools=_filter_function_tools(body.get("tools")),
    )


def _chat_role(role: str) -> str:
    return "system" if role == "developer" else role


def _flatten_content_blocks(blocks: list) -> str | list:
    text_blocks = []
    plain_texts = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "input_text":
            text = block.get("text", "")
            if any(key not in {"type", "text"} for key in block):
                text_block = {"type": "text", "text": text}
                for key, value in block.items():
                    if key not in {"type", "text"}:
                        text_block[key] = value
                text_blocks.append(text_block)
            else:
                plain_texts.append(text)
    if text_blocks:
        return [{"type": "text", "text": text} for text in plain_texts] + text_blocks
    return "\n".join(plain_texts)


def _is_empty_content(content) -> bool:
    return isinstance(content, str) and not content.strip()


def _filter_function_tools(tools: list | None) -> list | None:
    if tools is None:
        return None
    return [t for t in tools if isinstance(t, dict) and t.get("type") == "function"]


def _chat_tool_call(item: dict) -> dict:
    return {
        "id": item.get("call_id"),
        "type": "function",
        "function": {
            "name": item.get("name"),
            "arguments": item.get("arguments", ""),
        },
    }


def _extract_tool_calls(content: list) -> tuple[list[dict], list]:
    """Extract tool_use content blocks and convert to Chat Completions tool_calls format."""
    tool_calls = []
    filtered = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id"),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                },
            })
        else:
            filtered.append(block)
    return tool_calls, filtered


def responses_stream_event_from_ir(event: StreamEventIR) -> tuple[str, dict] | None:
    if event.type == "output_text_delta" and event.delta is not None:
        return "response.output_text.delta", {"type": "response.output_text.delta", "delta": event.delta}
    if event.type == "response_created" and event.response is not None:
        return "response.created", {"type": "response.created", "response": event.response}
    if event.type == "response_completed" and event.response is not None:
        return "response.completed", {"type": "response.completed", "response": event.response}
    return None


def responses_response_from_ir(response: ResponseIR) -> dict:
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "total_tokens": response.usage.total_tokens,
    }
    if response.usage.cache_read_tokens is not None:
        usage["input_tokens_details"] = {"cached_tokens": response.usage.cache_read_tokens}
    response_id = response.id or "resp_chatcmpl"
    output = [{
        "id": f"msg_{response_id}",
        "type": "message",
        "role": "assistant",
        "status": response.status,
        "content": [{"type": "output_text", "text": response.output_text, "annotations": []}],
    }]
    for tool_call in response.tool_calls or []:
        function = tool_call.get("function", {})
        output.append({
            "id": tool_call.get("id"),
            "type": "function_call",
            "call_id": tool_call.get("id"),
            "name": function.get("name"),
            "arguments": function.get("arguments", ""),
            "status": response.status,
        })
    return {
        "id": response_id,
        "object": "response",
        "created_at": response.created_at,
        "model": response.model,
        "status": response.status,
        "output": output,
        "output_text": response.output_text,
        "usage": usage,
    }
