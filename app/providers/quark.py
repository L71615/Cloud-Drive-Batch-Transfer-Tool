# -*- coding: utf-8 -*-
"""夸克网盘适配器。协议事实见 docs/spec/quark.md。"""
from __future__ import annotations

import re

from . import register
from ._http import Client
from .base import Entry, Provider, ProviderNotReady, ShareRef

SHARE_RE = re.compile(r"pan\.quark\.cn/s/([0-9A-Za-z]{6,})")
PWD_RE = re.compile(r"(?:pwd|提取码|密码)\s*[=:：]?\s*([0-9A-Za-z]{4})", re.I)


@register
class QuarkProvider(Provider):
    name = "quark"
    qps = 1.0

    API = {
        "share_token": "",  # spec §3
        "share_list": "",   # spec §4
        "transfer": "",     # spec §5
        "list_dir": "",     # spec §6
        "quota": "",        # spec §6
    }

    @property
    def caps(self) -> dict:
        return {"batch": True, "dedup": True, "max_batch": 50}

    def _url(self, key: str) -> str:
        url = self.API.get(key, "")
        if not url:
            raise ProviderNotReady(
                f"quark.{key} 未配置：先补 docs/spec/quark.md §{key}，再填 API['{key}']"
            )
        return url

    def parse_links(self, text: str) -> list[ShareRef]:
        refs: list[ShareRef] = []
        seen: set[str] = set()
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
        client = Client(self.name, self.credential, self.qps, referer="https://pan.quark.cn/")
        token = client.request("POST", self._url("share_token"), body={"pwd_id": ref.share_id})
        stoken = (token or {}).get("data", {}).get("stoken", "") if isinstance(token, dict) else ""
        data = client.request("GET", self._url("share_list"), params={
            "pwd_id": ref.share_id, "stoken": stoken, "pdir_fid": "0", "page": 1, "size": 100,
        })
        return self._entries(data)

    def _entries(self, data) -> list[Entry]:
        raise ProviderNotReady("quark: 响应字段映射未填，见 docs/spec/quark.md §4.2")

    def transfer(self, ref: ShareRef, entry: Entry, target: str) -> str:
        client = Client(self.name, self.credential, self.qps, referer="https://pan.quark.cn/")
        client.request("POST", self._url("transfer"), body={
            "fid_list": [entry.digest], "to_pdir_fid": target, "pwd_id": ref.share_id,
        })
        return f"{target.rstrip('/')}/{entry.path.rsplit('/', 1)[-1]}"
