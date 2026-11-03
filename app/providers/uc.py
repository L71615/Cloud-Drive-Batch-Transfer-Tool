# -*- coding: utf-8 -*-
"""UC 网盘（drive.uc.cn）适配器。

和夸克接口同构但端点域名、pr 参数、字段口径各自独立，见 docs/spec/uc.md。
不要因为"长得像"就直接复用夸克的常量——两边都要各自实机校验。
"""
from __future__ import annotations

import re

from . import register
from ._clouddrive import CloudDrive
from .base import ShareRef

LINK_RE = re.compile(r"https?://(?:drive|fast)\.uc\.cn/s/([0-9A-Za-z]{6,})")
PWD_RE = re.compile(r"(?:pwd|passcode|提取码|密码)\s*[=:：]?\s*([0-9A-Za-z]{4})", re.I)


@register
class UcProvider(CloudDrive):
    name = "uc"
    label = "UC网盘"
    color = "#ff6a00"
    short = "UC"
    qps = 1.0

    BASE = "https://pc-api.uc.cn"
    PR = "UCBrowser"
    FR = "pc"
    REFERER = "https://drive.uc.cn/"

    def parse_links(self, text: str) -> list[ShareRef]:
        out: list[ShareRef] = []
        seen: set[str] = set()
        for line in str(text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            m = PWD_RE.search(line)
            pwd = m.group(1) if m else ""
            for m in LINK_RE.finditer(line):
                if m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                out.append(ShareRef(provider=self.name, share_id=m.group(1), url=m.group(0), passcode=pwd))
        return out
