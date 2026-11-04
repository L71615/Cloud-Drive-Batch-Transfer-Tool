# -*- coding: utf-8 -*-
"""令牌桶限速：每个 provider / 每个账号一份，避免把对方服务打爆。"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate: float, burst: float | None = None):
        self.rate = max(0.05, float(rate))
        self.burst = float(burst if burst is not None else max(1.0, self.rate * 2))
        self._tokens = self.burst
        self._ts = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        delta = now - self._ts
        if delta > 0:
            self._tokens = min(self.burst, self._tokens + delta * self.rate)
            self._ts = now

    def acquire(self, tokens: float = 1.0, timeout: float | None = 30.0) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                need = (tokens - self._tokens) / self.rate
            if deadline is not None and time.monotonic() + need > deadline:
                return False
            time.sleep(min(need, 0.25))


_BUCKETS: dict[str, TokenBucket] = {}
_GUARD = threading.Lock()


def bucket(name: str, rate: float) -> TokenBucket:
    """取（或创建）一个按名字共享的令牌桶；rate 变化会立即生效。

    之前只在创建时读一次 rate，导致设置里把 QPS 从 0.8 调到 2.5 不生效，
    压测时 1000 个文件要跑十几分钟。
    """
    rate = max(0.05, float(rate))
    with _GUARD:
        b = _BUCKETS.get(name)
        if b is None:
            b = _BUCKETS[name] = TokenBucket(rate)
        elif abs(b.rate - rate) > 1e-9:
            with b._lock:  # noqa: SLF001 - 同一模块内改内部状态
                b.rate = rate
                b.burst = max(1.0, rate * 2)
                b._tokens = min(b._tokens if hasattr(b, "_tokens") else b.burst, b.burst)
        return b
