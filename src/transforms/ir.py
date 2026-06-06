from dataclasses import dataclass
from typing import Any


@dataclass
class Message:
    role: str
    content: Any
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None


@dataclass
class RequestIR:
    model: str
    messages: list[Message]
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stream: bool = False
    tools: list[dict] | None = None


@dataclass
class StreamEventIR:
    type: str
    delta: str | None = None
    response: dict | None = None


@dataclass
class ResponseIR:
    id: str | None
    model: str | None
    output_text: str
    usage: Usage
    created_at: int | None = None
    status: str = "completed"
    tool_calls: list[dict] | None = None
