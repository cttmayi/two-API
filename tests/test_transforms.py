from src.transforms.openai_chat import chat_request_from_ir, chat_response_to_ir, chat_stream_event_to_ir
from src.transforms.openai_responses import responses_request_to_ir, responses_response_from_ir, responses_stream_event_from_ir


def test_responses_request_converts_to_chat_request_through_ir():
    request_ir = responses_request_to_ir({
        "model": "gpt-4o",
        "instructions": "You are helpful.",
        "input": "hello",
        "max_output_tokens": 12,
        "temperature": 0.7,
        "top_p": 0.9,
        "stream": True,
        "tools": [{
            "type": "function",
            "name": "get_weather",
            "description": "Get weather by city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }],
    })

    chat_body = chat_request_from_ir(request_ir)

    assert chat_body == {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 12,
        "temperature": 0.7,
        "top_p": 0.9,
        "stream": True,
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather by city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }],
    }


def test_responses_codex_request_converts_to_chat_compatible_request():
    request_ir = responses_request_to_ir({
        "model": "gpt-4o",
        "input": [
            {"role": "developer", "content": [{"type": "input_text", "text": "follow rules"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            {
                "type": "function_call",
                "call_id": "call_123",
                "name": "exec_command",
                "arguments": "{\"cmd\":\"pwd\"}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_123",
                "output": "repo files",
            },
        ],
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
            },
            {"type": "namespace", "name": "multi_agent_v1", "tools": []},
            {"type": "web_search"},
        ],
    })

    chat_body = chat_request_from_ir(request_ir)

    assert chat_body == {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "follow rules"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "exec_command", "arguments": "{\"cmd\":\"pwd\"}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call_123", "content": "repo files"},
        ],
        "tools": [{
            "type": "function",
            "function": {
                "name": "exec_command",
                "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
            },
        }],
    }


def test_chat_stream_event_converts_to_responses_stream_event_through_ir():
    event_ir = chat_stream_event_to_ir({"choices": [{"delta": {"content": "hi"}}]})

    event = responses_stream_event_from_ir(event_ir)

    assert event == ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "hi"})


def test_chat_response_converts_to_responses_response_through_ir():
    response_ir = chat_response_to_ir({
        "id": "chatcmpl_123",
        "created": 1234567890,
        "model": "gpt-4o",
        "choices": [{"message": {"role": "assistant", "content": "hi there"}}],
        "usage": {
            "prompt_tokens": 7,
            "completion_tokens": 3,
            "total_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 2},
        },
    }, model="gpt-4o")

    responses_body = responses_response_from_ir(response_ir)

    assert responses_body["id"] == "resp_123"
    assert responses_body["object"] == "response"
    assert responses_body["created_at"] == 1234567890
    assert responses_body["model"] == "gpt-4o"
    assert responses_body["status"] == "completed"
    assert responses_body["output_text"] == "hi there"
    assert responses_body["output"][0]["content"][0]["text"] == "hi there"
    assert responses_body["usage"] == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
        "input_tokens_details": {"cached_tokens": 2},
    }


def test_chat_tool_calls_convert_to_responses_function_calls_through_ir():
    response_ir = chat_response_to_ir({
        "id": "chatcmpl_123",
        "created": 1234567890,
        "model": "gpt-4o",
        "choices": [{"message": {
            "role": "assistant",
            "content": "I will run it.",
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {"name": "exec_command", "arguments": "{\"cmd\":\"pwd\"}"},
            }],
        }}],
    }, model="gpt-4o")

    responses_body = responses_response_from_ir(response_ir)

    assert responses_body["output"] == [
        {
            "id": "msg_resp_123",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "I will run it.", "annotations": []}],
        },
        {
            "id": "call_123",
            "type": "function_call",
            "call_id": "call_123",
            "name": "exec_command",
            "arguments": "{\"cmd\":\"pwd\"}",
            "status": "completed",
        },
    ]
