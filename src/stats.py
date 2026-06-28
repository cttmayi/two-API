import os
import threading
import time
import json
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))

def _truncate_content(obj):
    """Return obj as-is, no truncation."""
    return obj


def _truncate_text(v):
    return v


def _convert_old_aliases(aliases: dict) -> dict:
    """Convert old flat alias format to new nested alias->model->stats format."""
    converted = {}
    for key, value in aliases.items():
        if isinstance(value, dict) and "model" in value:
            # Old format: {"alias": {"model": "gpt-4o", "requests": 1, ...}}
            model_name = value.pop("model")
            converted.setdefault(key, {})[model_name] = value
        else:
            # Already new format or empty
            converted[key] = value
    return converted


class Stats:
    def __init__(self, usage_path: str | None = None):
        self._lock = threading.Lock()
        self._started_at = time.time()
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
                aliases = _convert_old_aliases(item.get("aliases") or {})
                self._hourly[hour] = {
                    "hour": hour,
                    "requests": item.get("requests") or 0,
                    "prompt_tokens": item.get("prompt_tokens") or 0,
                    "completion_tokens": item.get("completion_tokens") or 0,
                    "cache_read_tokens": item.get("cache_read_tokens") or 0,
                    "cache_write_tokens": item.get("cache_write_tokens") or 0,
                    "total_tokens": item.get("total_tokens") or 0,
                    "total_latency_ms": item.get("total_latency_ms") or 0,
                    "aliases": aliases,
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
               cache_write_tokens: int | None = None,
               alias: str | None = None):
        alias_name = alias if alias is not None else ""
        with self._lock:
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
                    "aliases": {},
                }
            h = self._hourly[hour]
            h.setdefault("aliases", {})
            if alias_name not in h["aliases"]:
                h["aliases"][alias_name] = {}
            if model not in h["aliases"][alias_name]:
                h["aliases"][alias_name][model] = {
                    "provider": provider,
                    "requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "total_tokens": 0,
                    "total_latency_ms": 0,
                }
            ha = h["aliases"][alias_name][model]
            h["requests"] += 1
            ha["requests"] += 1
            if latency_ms:
                h["total_latency_ms"] += latency_ms
                ha["total_latency_ms"] += latency_ms
            if prompt_tokens:
                h["prompt_tokens"] += prompt_tokens
                h["total_tokens"] += prompt_tokens
                ha["prompt_tokens"] += prompt_tokens
                ha["total_tokens"] += prompt_tokens
            if completion_tokens:
                h["completion_tokens"] += completion_tokens
                h["total_tokens"] += completion_tokens
                ha["completion_tokens"] += completion_tokens
                ha["total_tokens"] += completion_tokens
            if cache_read_tokens:
                h["cache_read_tokens"] += cache_read_tokens
                ha["cache_read_tokens"] += cache_read_tokens
            if cache_write_tokens:
                h["cache_write_tokens"] += cache_write_tokens
                ha["cache_write_tokens"] += cache_write_tokens
            self._save_hourly_usage()

    def record_detail(self, model: str, provider: str, streaming: bool,
                      latency_ms: int, status: int,
                      prompt_tokens: int | None, completion_tokens: int | None,
                      cache_read: int | None, cache_write: int | None,
                      input_messages: list, output_content,
                      request_body=None,
                      alias: str | None = None,
                      path: str | None = None):
        with self._lock:
            now = datetime.now(TZ).strftime("%H:%M:%S")
            entry = {
                "time": now,
                "model": model,
                "alias": alias if alias is not None else "",
                "provider": provider,
                "path": path or "",
                "streaming": streaming,
                "status": status,
                "latency_ms": latency_ms,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cache_read": cache_read,
                "cache_write": cache_write,
                "input_messages": _truncate_content(input_messages),
                "output": _truncate_content(output_content),
            }
            if request_body is not None:
                entry["request_body"] = _truncate_content(request_body)
            self._recent.appendleft(entry)

    def snapshot(self) -> dict:
        with self._lock:
            hourly = []
            hour_keys = sorted(self._hourly)
            if hour_keys:
                earliest = datetime.strptime(hour_keys[0], "%Y-%m-%d %H:%M")
                latest = datetime.strptime(hour_keys[-1], "%Y-%m-%d %H:%M")
                current = max(earliest, latest - timedelta(hours=23))
                while current <= latest:
                    hour = current.strftime("%Y-%m-%d %H:%M")
                    item = dict(self._hourly.get(hour) or {
                        "hour": hour,
                        "requests": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "total_tokens": 0,
                        "total_latency_ms": 0,
                        "aliases": {},
                    })
                    item["avg_latency_ms"] = round(item["total_latency_ms"] / item["requests"], 1) if item["requests"] else 0
                    item["latency_per_output_token_ms"] = round(item["completion_tokens"] * 1000 / item["total_latency_ms"], 1) if item["completion_tokens"] and item["total_latency_ms"] else 0
                    item["aliases"] = {}
                    for alias_name, models in self._hourly.get(hour, {}).get("aliases", {}).items():
                        item["aliases"][alias_name] = {}
                        for model_name, model_data in models.items():
                            model_item = dict(model_data)
                            model_item.setdefault("total_latency_ms", 0)
                            model_item["avg_latency_ms"] = round(model_item["total_latency_ms"] / model_item["requests"], 1) if model_item["requests"] else 0
                            model_item["latency_per_output_token_ms"] = round(model_item["completion_tokens"] * 1000 / model_item["total_latency_ms"], 1) if model_item["completion_tokens"] and model_item["total_latency_ms"] else 0
                            item["aliases"][alias_name][model_name] = model_item
                    hourly.append(item)
                    current += timedelta(hours=1)

            total_requests = sum(
                model_data["requests"]
                for h in self._hourly.values()
                for models in h.get("aliases", {}).values()
                for model_data in models.values()
            )

            # Compute model cards from aliases
            models = {}
            for h in self._hourly.values():
                for models_dict in h.get("aliases", {}).values():
                    for model_name, model_data in models_dict.items():
                        if model_name not in models:
                            models[model_name] = {
                                "provider": model_data.get("provider", ""),
                                "requests": 0,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "cache_read_tokens": 0,
                                "cache_write_tokens": 0,
                                "total_latency_ms": 0,
                            }
                        m = models[model_name]
                        m["requests"] += model_data.get("requests", 0)
                        m["prompt_tokens"] += model_data.get("prompt_tokens", 0)
                        m["completion_tokens"] += model_data.get("completion_tokens", 0)
                        m["cache_read_tokens"] += model_data.get("cache_read_tokens", 0)
                        m["cache_write_tokens"] += model_data.get("cache_write_tokens", 0)
                        m["total_latency_ms"] += model_data.get("total_latency_ms", 0)

            return {
                "uptime_seconds": int(time.time() - self._started_at),
                "total_requests": total_requests,
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