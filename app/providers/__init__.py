# -*- coding: utf-8 -*-
"""Provider 注册表。新增网盘 = 新建一个模块 + 在这里登记一行。"""
from __future__ import annotations

from .base import Entry, Provider, ProviderError, ProviderNotReady, ShareRef, TransferResult

REGISTRY: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    REGISTRY[cls.name] = cls
    return cls


def create(name: str, credential: str = "") -> Provider:
    cls = REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"unknown provider: {name}")
    return cls(credential)


def names() -> list[str]:
    return sorted(REGISTRY)


def apply_dev_gateway(base: str) -> None:
    """把三个真实 provider 指向本地假网盘（tools/mock_netdisk.py）。

    仅用于离线联调与演示：`python run.py --gateway http://127.0.0.1:8899`。
    真实使用时不要设置，BASE 默认是各网盘的正式域名。
    """
    base = base.rstrip("/")
    REGISTRY["baidu"].BASE = base + "/baidu"
    REGISTRY["quark"].BASE = base + "/cd"
    REGISTRY["uc"].BASE = base + "/cd"


# 导入即注册
from . import mock, baidu, quark, uc  # noqa: E402,F401

__all__ = [
    "REGISTRY", "register", "create", "names", "apply_dev_gateway",
    "Provider", "ProviderError", "ProviderNotReady", "ShareRef", "Entry", "TransferResult",
]
