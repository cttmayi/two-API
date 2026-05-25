import threading
import time


class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._models: dict[str, dict] = {}

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

    def snapshot(self) -> dict:
        with self._lock:
            models = {}
            for name, m in self._models.items():
                models[name] = dict(m)
            return {
                "uptime_seconds": int(time.time() - self._started_at),
                "total_requests": sum(m["requests"] for m in self._models.values()),
                "models": models,
            }


_stats: Stats | None = None


def get_stats() -> Stats:
    global _stats
    if _stats is None:
        _stats = Stats()
    return _stats
