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
    with _GUARD:
        b = _BUCKETS.get(name)
        if b is None:
            b = _BUCKETS[name] = TokenBucket(rate)
        return b
