"""Невеликий in-process TTL-кеш метаданих каталогу (профілі артистів тощо).

Властивості: обмежений розмір (LRU), короткий TTL для порожніх відповідей, single-flight
(паралельні запити одного ключа викликають провайдера один раз), помилки (винятки) не кешуються,
значення повертаються як глибокі копії, тож виклики не можуть зіпсувати кеш.
НЕ використовувати для URL потоків, персональних даних чи результатів, що залежать від користувача.
"""
import copy
import threading
import time
from collections import OrderedDict

DEFAULT_TTL = 900      # 15 хв для знайдених метаданих
NEGATIVE_TTL = 60      # 1 хв для None/порожнього результату
MAX_ENTRIES = 2048

_lock = threading.Lock()
_entries: "OrderedDict[tuple, tuple[float, object]]" = OrderedDict()
_key_locks: dict = {}
_stats = {"hits": 0, "misses": 0}


def _now() -> float:
    return time.monotonic()


def _lookup(key):
    entry = _entries.get(key)
    if not entry:
        return False, None
    if entry[0] <= _now():
        _entries.pop(key, None)
        return False, None
    _entries.move_to_end(key)
    return True, entry[1]


def cached(namespace: str, key, loader, ttl: float = DEFAULT_TTL, negative_ttl: float = NEGATIVE_TTL):
    """Повертає значення з кешу або викликає loader() один раз на ключ. Винятки не кешуються."""
    full_key = (namespace, str(key))
    with _lock:
        hit, value = _lookup(full_key)
        if hit:
            _stats["hits"] += 1
            return copy.deepcopy(value)
        key_lock = _key_locks.setdefault(full_key, threading.Lock())
    with key_lock:
        with _lock:
            hit, value = _lookup(full_key)
            if hit:
                _stats["hits"] += 1
                return copy.deepcopy(value)
        try:
            value = loader()
        except BaseException:
            with _lock:
                _key_locks.pop(full_key, None)
            raise
        lifetime = ttl if value else negative_ttl
        with _lock:
            _stats["misses"] += 1
            _entries[full_key] = (_now() + lifetime, copy.deepcopy(value))
            _entries.move_to_end(full_key)
            while len(_entries) > MAX_ENTRIES:
                _entries.popitem(last=False)
            _key_locks.pop(full_key, None)
        return value


def invalidate(namespace: str, key) -> None:
    with _lock:
        _entries.pop((namespace, str(key)), None)


def clear() -> None:
    with _lock:
        _entries.clear()
        _key_locks.clear()
        _stats.update(hits=0, misses=0)


def stats() -> dict:
    with _lock:
        return {**_stats, "size": len(_entries)}