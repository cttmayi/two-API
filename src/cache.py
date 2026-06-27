import hashlib
import json
import time
from dataclasses import dataclass
from cachetools import TTLCache


@dataclass
class CacheEntry:
    response_body: bytes | None = None
    sse_lines: list[str] | None = None
    created_at: float = 0.0
    hit_count: int = 0


class CacheConfig:
    def __init__(self, enabled: bool = True, ttl_seconds: int = 3600,
                 max_entries: int = 2000, aliases: list[str] | None = None,
                 key_fields: list[str] | None = None):
        self.enabled = enabled
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.aliases = aliases or []
        self.key_fields = key_fields or []


class CacheStore:
    def __init__(self, config: CacheConfig):
        self._config = config
        ttl = config.ttl_seconds if config.ttl_seconds > 0 else None
        self._cache = TTLCache(maxsize=config.max_entries, ttl=ttl)
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> CacheEntry | None:
        entry = self._cache.get(key)
        if entry is not None:
            entry.hit_count += 1
            self._hits += 1
        else:
            self._misses += 1
        return entry

    def set(self, key: str, entry: CacheEntry):
        self._cache[key] = entry

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def enabled(self) -> bool:
        return self._config.enabled


_cache_store: CacheStore | None = None


def init_cache(config: CacheConfig) -> CacheStore:
    global _cache_store
    _cache_store = CacheStore(config)
    return _cache_store


def get_cache() -> CacheStore:
    global _cache_store
    if _cache_store is None:
        _cache_store = CacheStore(CacheConfig(enabled=False))
    return _cache_store


def reset_cache():
    global _cache_store
    _cache_store = None


def _build_cache_key(alias: str, body: dict, key_fields: list[str], path: str = "") -> str:
    parts: dict = {}
    if "messages" in body:
        parts["messages"] = body["messages"]
    elif "input" in body:
        parts["input"] = body["input"]
    for field in sorted(key_fields):
        if field == "alias":
            parts["alias"] = alias
        elif field in body:
            parts[field] = body[field]
    if path:
        parts["_path"] = path
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _should_cache(config: CacheConfig, alias: str, key_fields: list[str]) -> bool:
    if not config.enabled:
        return False
    if "alias" in key_fields and not alias:
        return False
    if config.aliases and alias not in config.aliases:
        return False
    return True


async def stream_from_cache(entry: CacheEntry):
    for line in (entry.sse_lines or []):
        yield (line + "\n").encode()
