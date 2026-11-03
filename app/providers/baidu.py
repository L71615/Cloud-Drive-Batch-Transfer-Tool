# -*- coding: utf-8 -*-
"""百度网盘适配器。

协议事实来自 docs/spec/baidu.md（自写规格）。端点集中放在 API 字典里：
填它的时候只看规格，不看任何反汇编输出——这是洁净室边界。
"""
from __future__ import annotations

import re

from . import register
from ._http import Client
from .base import Entry, Provider, ProviderNotReady, ShareRef

SHARE_RE = re.compile(r"pan\.baidu\.com/s/([0-9A-Za-z_-]{4,})")
PWD_RE = re.compile(r"(?:pwd|提取码)\s*[=:：]?\s*([0-9A-Za-z]{4})", re.I)


@register
class BaiduProvider(Provider):
    name = "baidu"
    qps = 0.8

    API = {
        "share_info": "",   # spec §3
        "share_list": "",   # spec §4
        "transfer": "",     # spec §5
        "list_dir": "",     # spec §6
        "quota": "",        # spec §6
    }

    @property
    def caps(self) -> dict:
        return {"batch": True, "dedup": True, "max_batch": 30}

    def _url(self, key: str) -> str:
        url = self.API.get(key, "")
        if not url:
            raise ProviderNotReady(
                f"baidu.{key} 未配置：先补 docs/spec/baidu.md §{key}，再填 API['{key}']"
            )
        return url

    def parse_links(self, text: str) -> list[ShareRef]:
        refs: list[ShareRef] = []
        seen: set[str] = set()
        # 提取码可能出现在链接后面，按整段文本就近匹配
        for m in SHARE_RE.finditer(text):
            sid = m.group(1)
            if sid in seen:
                continue
            seen.add(sid)
            tail = text[m.end(): m.end() + 40]
            pwd = PWD_RE.search(tail)
            refs.append(ShareRef(self.name, sid, m.group(0), pwd.group(1) if pwd else "", ""))
        return refs

    def list_share(self, ref: ShareRef) -> list[Entry]:
        client = Client(self.name, self.credential, self.qps, referer="https://pan.baidu.com/")
        client.request("GET", self._url("share_info"), params={"surl": ref.share_id, "pwd": ref.passcode})
        data = client.request("GET", self._url("share_list"), params={
            "shorturl": ref.share_id, "dir": "/", "page": 1, "num": 100, "pwd": ref.passcode,
        })
        return self._entries(data)

    def _entries(self, data) -> list[Entry]:
        raise ProviderNotReady("baidu: 响应字段映射未填，见 docs/spec/baidu.md §4.2")

    def transfer(self, ref: ShareRef, entry: Entry, target: str) -> str:
        client = Client(self.name, self.credential, self.qps, referer="https://pan.baidu.com/")
        client.request("POST", self._url("transfer"), form=True, body={
            "fsidlist": entry.digest or entry.path, "path": target,
        })
        return f"{target.rstrip('/')}/{entry.path.rsplit('/', 1)[-1]}"
