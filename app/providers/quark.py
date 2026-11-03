# -*- coding: utf-8 -*-
"""夸克网盘（pan.quark.cn）适配器。

协议细节见 docs/spec/quark.md；本文件只做"规格 → 代码"的翻译，不含任何
第三方实现代码。端点与字段若与线上不符，先改规格，再改这里。
"""
from __future__ import annotations

import re

from . import register
from ._clouddrive import CloudDrive
from .base import ShareRef

LINK_RE = re.compile(r"https?://pan\.quark\.cn/s/([0-9A-Za-z]{6,})")
PWD_RE = re.compile(r"(?:pwd|passcode|提取码|密码)\s*[=:：]?\s*([0-9A-Za-z]{4})", re.I)
BARE_RE = re.compile(r"^([0-9A-Za-z]{6,})$")


@register
class QuarkProvider(CloudDrive):
    name = "quark"
    label = "夸克网盘"
    color = "#3b82f6"
    short = "夸克"
    qps = 1.0

    BASE = "https://drive-pc.quark.cn"
    PR = "ucpro"
    FR = "pc"
    REFERER = "https://pan.quark.cn/"

    def parse_links(self, text: str) -> list[ShareRef]:
        return parse_share_links(text, self.name)


def parse_share_links(text: str, provider: str = "quark") -> list[ShareRef]:
    out: list[ShareRef] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        pwd = ""
        m = PWD_RE.search(line)
        if m:
            pwd = m.group(1)
        for m in LINK_RE.finditer(line):
            out.append(ShareRef(provider=provider, share_id=m.group(1), url=m.group(0), passcode=pwd))
        if not LINK_RE.search(line):
            m = BARE_RE.match(line)
            if m:
                out.append(ShareRef(provider=provider, share_id=m.group(1), passcode=pwd))
    # 去重（同一 share_id 只保留一次）
    seen, uniq = set(), []
    for r in out:
        if r.share_id in seen:
            continue
        seen.add(r.share_id)
        uniq.append(r)
    return uniq
