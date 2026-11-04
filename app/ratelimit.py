# -*- coding: utf-8 -*-
"""令牌桶限速：每个 provider / 每个账号一份，避免把对方服务打爆。

自适应：设置里的 QPS 是**上限**，实际速率由服务端反馈决定——
被限速就减半，连续顺利就慢慢爬回去。用户不需要知道该填几。
"""
from __future__ import annotations

import threading
import time

FLOOR = 0.3      # 再慢也不低于这个速率，否则等于停摆
REWARD_EVERY = 15  # 连续这么多次成功，往上探一点


class TokenBucket:
    def __init__(self, rate: float, burst: float | None = None):
        self.ceiling = max(0.05, float(rate))   # 上限：设置页里填的 QPS
        # 起步不要直接冲到上限，先跑到一半，让服务端有反应时间
        self.rate = max(FLOOR, min(self.ceiling, max(1.0, self.ceiling * 0.5)))
        self.burst = float(burst if burst is not None else max(1.0, self.rate * 2))
        self._tokens = self.burst
        self._ts = time.monotonic()
        self._lock = threading.Lock()
        self._ok = 0
        self.penalties = 0

    # ---------------- 额度 ----------------
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

    # ---------------- 自适应 ----------------
    def penalize(self, retry_after: float | None = None) -> None:
        """被限速：速率减半，必要时先罚站 retry_after 秒。"""
        with self._lock:
            self.rate = max(FLOOR, self.rate * 0.5)
            self.burst = max(1.0, self.rate * 2)
            self._ok = 0
            self.penalties += 1
            if retry_after and retry_after > 0:
                self._tokens = 0.0
                self._ts = time.monotonic() + min(float(retry_after), 60.0)

    def reward(self) -> None:
        """顺利跑了 REASON 次：慢慢把速率往上限方向抬。"""
        with self._lock:
            self._ok += 1
            if self._ok < REWARD_EVERY:
                return
            self._ok = 0
            if self.rate < self.ceiling:
                self.rate = min(self.ceiling, self.rate * 1.15)
                self.burst = max(1.0, self.rate * 2)

    def set_ceiling(self, rate: float) -> None:
        with self._lock:
            self.ceiling = max(0.05, float(rate))
            if self.rate > self.ceiling:
                self.rate = self.ceiling
                self.burst = max(1.0, self.rate * 2)

    def info(self) -> dict:
        with self._lock:
            return {
                "rate": round(self.rate, 2),
                "ceiling": round(self.ceiling, 2),
                "penalties": self.penalties,
            }


_BUCKETS: dict[str, TokenBucket] = {}
_GUARD = threading.Lock()


def bucket(name: str, rate: float) -> TokenBucket:
    """取（或创建）一个按名字共享的令牌桶；上限变化会立即生效。

    之前只在创建时读一次 rate，导致设置里把 QPS 从 0.8 调到 2.5 不生效，
    压测时 1000 个文件要跑十几分钟。
    """
    rate = max(0.05, float(rate))
    with _GUARD:
        b = _BUCKETS.get(name)
        if b is None:
            b = _BUCKETS[name] = TokenBucket(rate)
        elif abs(b.ceiling - rate) > 1e-9:
            b.set_ceiling(rate)
        return b


def snapshot() -> dict:
    with _GUARD:
        return {k: v.info() for k, v in _BUCKETS.items()}
