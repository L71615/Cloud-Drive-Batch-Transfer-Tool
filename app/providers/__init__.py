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


# 导入即注册
from . import mock, baidu, quark, uc  # noqa: E402,F401

__all__ = [
    "REGISTRY", "register", "create", "names",
    "Provider", "ProviderError", "ProviderNotReady", "ShareRef", "Entry", "TransferResult",
]
