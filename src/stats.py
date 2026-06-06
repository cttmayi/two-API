import os
import threading
import time
import json
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))

MAX_TEXT_LEN = 500


def _truncate_content(obj):
    """Truncate long text fields in content blocks to avoid storing huge payloads."""
    if isinstance(obj, dict):
        truncated = {}
        for k, v in obj.items():
            if k in ("text", "content", "name", "arguments", "partial_json",
                     "input", "_input_json", "thinking", "signature"):
                truncated[k] = _truncate_text(v)
            else:
                truncated[k] = _truncate_content(v)
        return truncated
    if isinstance(obj, list):
        return [_truncate_content(item) for item in obj]
    return obj


def _truncate_text(v):
    if isinstance(v, str) and len(v) > MAX_TEXT_LEN:
        return v[:MAX_TEXT_LEN] + "...[truncated]"
    if isinstance(v, dict):
        return _truncate_content(v)
    if isinstance(v, list):
        return [_truncate_content(item) for item in v]
    return v


class Stats:
    def __init__(self, usage_path: str | None = None):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._models: dict[str, dict] = {}
        self._recent: deque[dict] = deque(maxlen=50)
        self._hourly: dict[str, dict] = {}
        self._usage_path = os.path.expanduser(usage_path) if usage_path else None
        self._load_hourly_usage()

    def _load_hourly_usage(self):
        if not self._usage_path:
            return
        try:
            with open(self._usage_path, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        for item in data.get("hourly", []):
            hour = item.get("hour")
            if hour:
                self._hourly[hour] = {
                    "hour": hour,
                    "requests": item.get("requests") or 0,
                    "prompt_tokens": item.get("prompt_tokens") or 0,
                    "completion_tokens": item.get("completion_tokens") or 0,
                    "cache_read_tokens": item.get("cache_read_tokens") or 0,
                    "cache_write_tokens": item.get("cache_write_tokens") or 0,
                    "total_tokens": item.get("total_tokens") or 0,
                    "total_latency_ms": item.get("total_latency_ms") or 0,
                    "models": item.get("models") or {},
                }

    def _save_hourly_usage(self):
        if not self._usage_path:
            return
        Path(self._usage_path).parent.mkdir(parents=True, exist_ok=True)
        with open(self._usage_path, "w") as f:
            json.dump({"hourly": [dict(self._hourly[hour]) for hour in sorted(self._hourly)]}, f, ensure_ascii=False, indent=2)

    def record(self, model: str, provider: str, prompt_tokens: int | None,
               completion_tokens: int | None, latency_ms: int | None,
               cache_read_tokens: int | None = None,
               cache_write_tokens: int | None = None):
        with self._lock:
            if model not in self._models:
                self._models[model] = {
                    "provider": provider,
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_latency_ms": 0,
                }
            m = self._models[model]
            m["requests"] += 1
            if prompt_tokens:
                m["prompt_tokens"] += prompt_tokens
            if completion_tokens:
                m["completion_tokens"] += completion_tokens
            if cache_read_tokens:
                m["cache_read_tokens"] += cache_read_tokens
            if cache_write_tokens:
                m["cache_write_tokens"] += cache_write_tokens
            if latency_ms:
                m["total_latency_ms"] += latency_ms

            hour = datetime.now(TZ).replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
            if hour not in self._hourly:
                self._hourly[hour] = {
                    "hour": hour,
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 0,
                    "total_latency_ms": 0,
                    "models": {},
                }
            h = self._hourly[hour]
            h.setdefault("models", {})
            if model not in h["models"]:
                h["models"][model] = {
                    "provider": provider,
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 0,
                    "total_latency_ms": 0,
                }
            hm = h["models"][model]
            h["requests"] += 1
            hm["requests"] += 1
            if latency_ms:
                h["total_latency_ms"] += latency_ms
                hm["total_latency_ms"] += latency_ms
            if prompt_tokens:
                h["prompt_tokens"] += prompt_tokens
                h["total_tokens"] += prompt_tokens
                hm["prompt_tokens"] += prompt_tokens
                hm["total_tokens"] += prompt_tokens
            if completion_tokens:
                h["completion_tokens"] += completion_tokens
                h["total_tokens"] += completion_tokens
                hm["completion_tokens"] += completion_tokens
                hm["total_tokens"] += completion_tokens
            if cache_read_tokens:
                h["cache_read_tokens"] += cache_read_tokens
                hm["cache_read_tokens"] += cache_read_tokens
            if cache_write_tokens:
                h["cache_write_tokens"] += cache_write_tokens
                hm["cache_write_tokens"] += cache_write_tokens
            self._save_hourly_usage()

    def record_detail(self, model: str, provider: str, streaming: bool,
                      latency_ms: int, status: int,
                      prompt_tokens: int | None, completion_tokens: int | None,
                      cache_read: int | None, cache_write: int | None,
                      input_messages: list, output_content):
        with self._lock:
            now = datetime.now(TZ).strftime("%H:%M:%S")
            self._recent.appendleft({
                "time": now,
                "model": model,
                "provider": provider,
                "streaming": streaming,
                "status": status,
                "latency_ms": latency_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "input_messages": _truncate_content(input_messages),
                "output": _truncate_content(output_content),
            })

    def snapshot(self) -> dict:
        with self._lock:
            models = {}
            for name, m in self._models.items():
                models[name] = dict(m)
            hourly = []
            for hour in sorted(self._hourly)[-24:]:
                item = dict(self._hourly[hour])
                item["avg_latency_ms"] = round(item["total_latency_ms"] / item["requests"], 1) if item["requests"] else 0
                item["latency_per_output_token_ms"] = round(item["total_latency_ms"] / item["completion_tokens"], 1) if item["completion_tokens"] else 0
                item["models"] = {}
                for model, model_data in self._hourly[hour].get("models", {}).items():
                    model_item = dict(model_data)
                    model_item.setdefault("total_latency_ms", 0)
                    model_item["avg_latency_ms"] = round(model_item["total_latency_ms"] / model_item["requests"], 1) if model_item["requests"] else 0
                    model_item["latency_per_output_token_ms"] = round(model_item["total_latency_ms"] / model_item["completion_tokens"], 1) if model_item["completion_tokens"] else 0
                    item["models"][model] = model_item
                hourly.append(item)
            return {
                "uptime_seconds": int(time.time() - self._started_at),
                "total_requests": sum(m["requests"] for m in self._models.values()),
                "models": models,
                "recent": list(self._recent),
                "hourly": hourly,
            }


_stats: Stats | None = None


def init_stats(usage_path: str) -> Stats:
    global _stats
    _stats = Stats(usage_path)
    return _stats


def get_stats() -> Stats:
    global _stats
    if _stats is None:
        _stats = Stats()
    return _stats
