"""A small in-process TTL cache for read queries.

Data only changes when the pipeline or the models run, so caching identical
queries for a few minutes removes most database load from dashboard refreshes.
"""
from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Any, Callable

from ..config import get_settings

_store: dict[tuple, tuple[float, Any]] = {}
_lock = threading.Lock()


def cached(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        ttl = get_settings().api_cache_ttl_seconds
        if ttl <= 0:
            return func(*args, **kwargs)
        key = (func.__module__, func.__qualname__, args, tuple(sorted(kwargs.items())))
        now = time.monotonic()
        with _lock:
            hit = _store.get(key)
            if hit and hit[0] > now:
                return hit[1]
        value = func(*args, **kwargs)
        with _lock:
            _store[key] = (now + ttl, value)
        return value
    return wrapper


def clear() -> None:
    with _lock:
        _store.clear()
