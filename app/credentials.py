# -*- coding: utf-8 -*-
"""登录态（Cookie）的规范化与基础校验。

用户通常从浏览器 DevTools 里整段复制 `Cookie` 请求头，可能带着 "Cookie:" 前缀、
换行、多余空格。这里统一清洗，并在保存前检查关键字段是否存在，避免"存了个空壳
账号，跑任务时才报 401"。
"""
from __future__ import annotations

import re

# 每个网盘至少要有一个的 Cookie 字段（元组内为"任意其一即可"）
REQUIRED: dict[str, tuple[tuple[str, ...], ...]] = {
    "baidu": (("BDUSS",),),
    "quark": (("__pus", "__puus"),),
    "uc": (("__pus", "__puus"),),
}

_PROVIDER_LABEL = {"baidu": "百度网盘", "quark": "夸克网盘", "uc": "UC 网盘"}


def normalize(raw: str) -> str:
    """把整段粘贴内容清洗成 `a=b; c=d` 形式。"""
    s = str(raw or "").strip()
    s = re.sub(r"^\s*cookie\s*:\s*", "", s, flags=re.I)
    s = s.replace("\r", "\n")
    s = re.sub(r"\n+", "; ", s)
    s = re.sub(r"\s*;\s*", "; ", s)
    s = re.sub(r"\s*=\s*", "=", s)
    return s.strip(" ;")


def parse(cookie: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in normalize(cookie).split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def missing(provider: str, cookie: str) -> list[str]:
    """返回缺失的关键字段描述；空列表表示通过。"""
    groups = REQUIRED.get(provider)
    if not groups:
        return []
    got = parse(cookie)
    out = []
    for group in groups:
        if not any(k in got and got[k] for k in group):
            out.append(" 或 ".join(group))
    return out


def validate(provider: str, raw: str) -> dict:
    """保存前的统一入口：清洗 + 校验。"""
    cookie = normalize(raw)
    label = _PROVIDER_LABEL.get(provider, provider)
    if not cookie:
        return {"ok": False, "cookie": "", "missing": [], "error": "Cookie 不能为空"}
    if "=" not in cookie:
        return {"ok": False, "cookie": cookie, "missing": [],
                "error": "这看起来不是 Cookie：整段内容里没有 key=value"}
    lack = missing(provider, cookie)
    if lack:
        return {"ok": False, "cookie": cookie, "missing": lack,
                "error": f"缺少 {', '.join(lack)}；{label} 的登录态必须包含这些字段"}
    return {"ok": True, "cookie": cookie, "missing": [], "error": "",
            "count": len(parse(cookie))}
