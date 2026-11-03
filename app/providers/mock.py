# -*- coding: utf-8 -*-
"""Mock provider：让整条链路（解析→列表→转存→重试→统计）在无网络、
无真实账号的情况下可运行、可测试。也是 CI 的唯一执行路径。"""
from __future__ import annotations

import hashlib
import random
import time

from . import register
from .base import Entry, Provider, ProviderError, ShareRef


@register
class MockProvider(Provider):
    name = "mock"
    label = "演示模式"
    color = "#8b5cf6"
    short = "演示"
    qps = 5.0

    def check_auth(self) -> dict:
        return {"valid": True, "message": "演示模式无需登录态"}

    @property
    def caps(self) -> dict:
        return {"batch": True, "dedup": True, "max_batch": 100}

    def parse_links(self, text: str) -> list[ShareRef]:
        out: list[ShareRef] = []
        seen: set[str] = set()
        for token in str(text).replace(",", " ").split():
            token = token.strip()
            if not token or token in seen:
                continue
            seen.add(token)
            sid = hashlib.sha1(token.encode("utf-8")).hexdigest()[:12]
            out.append(ShareRef(self.name, sid, token, "", f"sample-{sid[:6]}"))
        return out

    def list_share(self, ref: ShareRef) -> list[Entry]:
        time.sleep(0.05)
        n = 3 + int(ref.share_id[:2], 16) % 4
        return [
            Entry(
                path=f"/{ref.title}/part-{i:02d}.bin",
                size=1024 * (i + 1),
                digest=hashlib.md5(f"{ref.share_id}:{i}".encode()).hexdigest(),
            )
            for i in range(n)
        ]

    def transfer(self, ref: ShareRef, entry: Entry, target: str) -> str:
        time.sleep(0.02)
        # 稳定复现一次失败，用于验证重试链路（真实适配器由网络自然产生）
        if "retry-me" in entry.path and not getattr(self, "_retried", False):
            self._retried = True
            raise ProviderError("mock: transient failure")
        if random.random() < 0.02:
            raise ProviderError("mock: random transient failure")
        return f"{target.rstrip('/')}/{entry.path.lstrip('/')}"
