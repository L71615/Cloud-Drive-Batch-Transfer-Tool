# -*- coding: utf-8 -*-
"""百度网盘（pan.baidu.com）适配器。

协议细节见 docs/spec/baidu.md。要点：
- 分享页先换 shareid / uk，带提取码的再加一步 verify 拿 sekey；
- 列目录与转存都是"先拿 fs_id 再批量提交"的两段式；
- 所有 errno 都走一张表翻译，未知码原样透出，方便补规格。
"""
from __future__ import annotations

import json
import re
from urllib.parse import unquote

from . import register
from ._http import Client
from .base import AuthExpired, Entry, Provider, ProviderError, ProviderNotReady, ShareRef

BASE = "https://pan.baidu.com"
APP_ID = "250528"

LINK_RE = re.compile(r"https?://pan\.baidu\.com/s/([0-9A-Za-z_\-]{4,})")
PWD_RE = re.compile(r"(?:pwd|提取码|密码)\s*[=:：]?\s*([0-9A-Za-z]{4})", re.I)

MAX_DEPTH = 8
MAX_ENTRIES = 5000

# errno 含义需随实机响应逐步收敛（spec §8），未收录的码会原样带出来
ERRNO = {
    0: "成功",
    2: "参数错误",
    -6: "需要验证（风控）",
    -7: "文件名非法",
    -9: "提取码错误",
    -62: "分享已失效",
    4: "请求过于频繁",
    12: "文件已存在",
    111: "空间不足",
    112: "转存数量超限",
    31066: "文件不存在",
}


@register
class BaiduProvider(Provider):
    name = "baidu"
    label = "百度网盘"
    color = "#2932e1"
    short = "百度"
    qps = 0.8
    BASE = "https://pan.baidu.com"

    def __init__(self, credential: str = ""):
        super().__init__(credential)
        self._client: Client | None = None
        self._share: dict[str, dict] = {}   # share_id -> {shareid, uk, sekey}

    @property
    def ready(self) -> bool:
        return True

    # ---------------- 基础设施 ----------------
    def _c(self) -> Client:
        if self._client is None:
            self._client = Client(self.name, self.credential, self.qps, referer="https://pan.baidu.com/")
        return self._client

    def _common(self) -> dict:
        return {"channel": "chunlei", "web": "1", "app_id": APP_ID, "clienttype": "0"}

    def _check(self, data: dict, what: str) -> dict:
        if not isinstance(data, dict):
            raise ProviderError(f"百度{what}：响应不是 JSON")
        errno = data.get("errno")
        if errno in (0, None):
            return data
        text = ERRNO.get(errno, str(data.get("errmsg") or data.get("show_msg") or "未知错误"))
        if errno in (-6, 4):
            raise ProviderError(f"百度{what}失败：{text}（errno={errno}）")
        if errno in (-62, -9, 31066):
            raise ProviderError(f"百度{what}失败：{text}（errno={errno}）")
        raise ProviderError(f"百度{what}失败：{text}（errno={errno}）")

    # ---------------- 分享 ----------------
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
                surl = m.group(1)
                if surl in seen:
                    continue
                seen.add(surl)
                out.append(ShareRef(provider=self.name, share_id=surl, url=m.group(0), passcode=pwd))
        return out

    def _verify(self, ref: ShareRef) -> str:
        """有提取码时先过校验，返回 sekey（= 解码后的 randsk）。"""
        if not ref.passcode:
            return ""
        data = self._c().request(
            "POST", self.BASE + "/share/verify",
            params={"surl": ref.share_id, "channel": "chunlei", "web": "1",
                    "app_id": APP_ID, "clienttype": "0"},
            body={"pwd": ref.passcode, "vcode": "", "vcode_str": ""},
            form=True,
        )
        self._check(data, "提取码校验")
        randsk = str(data.get("randsk") or "")
        return unquote(randsk)

    def share_meta(self, ref: ShareRef) -> dict:
        if ref.share_id in self._share:
            return self._share[ref.share_id]
        data = self._c().request(
            "GET", self.BASE + "/api/shorturlinfo",
            params={"shorturl": ref.share_id, **self._common()},
        )
        d = self._check(data, "分享信息")
        shareid = d.get("shareid")
        uk = d.get("uk")
        if not shareid or not uk:
            raise ProviderError("百度分享信息缺少 shareid / uk（分享可能已失效）")
        sekey = self._verify(ref)
        meta = {"shareid": str(shareid), "uk": str(uk), "sekey": sekey}
        self._share[ref.share_id] = meta
        return meta

    def _page_share(self, ref: ShareRef, meta: dict, dir_path: str, page: int) -> tuple[list[dict], int]:
        data = self._c().request("GET", self.BASE + "/share/list", params={
            "uk": meta["uk"], "shareid": meta["shareid"], "order": "other", "desc": "1",
            "showempty": "0", "page": str(page), "num": "100", "dir": dir_path,
            "t": "0", "bdstoken": "", **self._common(),
        })
        d = self._check(data, "列分享目录")
        return list(d.get("list") or []), len(d.get("list") or [])

    def list_share(self, ref: ShareRef) -> list[Entry]:
        meta = self.share_meta(ref)
        count = 0
        queue: list[tuple[str, str, int]] = [("/", "", 0)]
        while queue and count < MAX_ENTRIES:
            dir_path, prefix, depth = queue.pop(0)
            if depth > MAX_DEPTH:
                continue
            page = 1
            seen_fids: set[str] = set()
            while True:
                items, n = self._page_share(ref, meta, dir_path, page)
                if not items:
                    break
                fids = {str(it.get("fs_id") or "") for it in items}
                if fids and fids <= seen_fids:
                    break
                seen_fids |= fids
                for it in items:
                    name = str(it.get("server_filename") or "")
                    path = f"{prefix}/{name}" if prefix else f"/{name}"
                    if it.get("isdir"):
                        queue.append((str(it.get("path") or f"{dir_path.rstrip('/')}/{name}"), path, depth + 1))
                        continue
                    yield Entry(
                        path=path,
                        size=int(it.get("size") or 0),
                        digest=str(it.get("md5") or ""),
                        meta={"fs_id": it.get("fs_id"), "isdir": False},
                    )
                    count += 1
                    if count >= MAX_ENTRIES:
                        break
                if n < 100:
                    break
                page += 1
        return

    # ---------------- 我的目录 ----------------
    def _list_dir(self, path: str) -> list[dict]:
        data = self._c().request("GET", self.BASE + "/api/list", params={
            "dir": path, "order": "name", "num": "100", "page": "1", **self._common(),
        })
        d = self._check(data, "列目录")
        return list(d.get("list") or [])

    def ensure_dir(self, path: str) -> str:
        cur = ""
        for seg in [s for s in str(path or "").split("/") if s]:
            parent = cur or "/"
            cur = f"{cur}/{seg}"
            names = {str(x.get("server_filename")) for x in self._list_dir(parent) if x.get("isdir")}
            if seg in names:
                continue
            data = self._c().request(
                "POST", self.BASE + "/api/create",
                params={"a": "commit", **self._common()},
                body={"path": cur, "isdir": "1", "block_list": "[]", "size": "0"},
                form=True,
            )
            d = self._check(data, "建目录")
            if d.get("errno") not in (0, None) and d.get("errno") != 12:
                raise ProviderError(f"创建目录失败：{cur}")
        return cur or "/"

    # ---------------- 转存 ----------------
    def transfer(self, ref: ShareRef, entry: Entry, target: str) -> str:
        return self.transfer_batch(ref, [entry], target)[0]

    def transfer_batch(self, ref: ShareRef, entries: list, target: str) -> list[str]:
        meta = self.share_meta(ref)
        dest = self.ensure_dir(target)
        ids = [e.meta.get("fs_id") for e in entries if e.meta.get("fs_id") not in (None, "")]
        if not ids:
            raise ProviderError("缺少 fs_id，无法转存")
        data = self._c().request(
            "POST", self.BASE + "/share/transfer",
            params={
                "shareid": meta["shareid"], "from": meta["uk"], "sekey": meta["sekey"],
                "ondup": "newcopy", "async": "1", **self._common(),
            },
            body={"fsidlist": json.dumps(ids), "path": dest},
            form=True,
        )
        d = self._check(data, "转存")
        got = [str(item.get("to")) for item in (d.get("extra") or {}).get("list") or [] if item.get("to")]
        base = dest.rstrip("/")
        out = []
        for e in entries:
            name = e.path.rsplit("/", 1)[-1]
            match = next((g for g in got if g.endswith("/" + name)), "")
            out.append(match or f"{base}/{name}")
        return out

    # ---------------- 其它 ----------------
    def list_dir(self, path: str = "/") -> list[Entry]:
        return [
            Entry(
                path=f"/{x.get('server_filename')}",
                size=int(x.get("size") or 0),
                digest=str(x.get("md5") or ""),
                meta={"fs_id": x.get("fs_id"), "isdir": bool(x.get("isdir"))},
            )
            for x in self._list_dir(path)
        ]

    def browse(self, path: str = "/") -> list[dict]:
        base = str(path or "/").rstrip("/")
        out = []
        for x in self._list_dir(path or "/"):
            name = str(x.get("server_filename") or "")
            out.append({
                "name": name,
                "path": f"{base}/{name}",
                "is_dir": bool(x.get("isdir")),
                "size": int(x.get("size") or 0),
                "meta": {"fs_id": x.get("fs_id")},
            })
        return out

    def quota(self) -> dict:
        data = self._c().request("GET", self.BASE + "/api/quota", params={
            "checkfree": "1", "checkexpire": "1", **self._common(),
        })
        d = self._check(data, "查询容量")
        total = int(d.get("total") or 0)
        used = int(d.get("used") or 0)
        return {"total": total, "used": used, "free": max(0, total - used)}

    def check_auth(self) -> dict:
        if not self.credential:
            raise ProviderNotReady("还没有配置百度登录态（Cookie）")
        try:
            self._list_dir("/")
        except AuthExpired as e:
            return {"valid": False, "message": f"登录态已失效：{e}"}
        return {"valid": True, "message": "登录态有效"}

    def diagnose(self, ref: ShareRef) -> list[dict]:
        steps: list[dict] = []
        meta: dict = {}
        self._run_step(steps, "解析链接", lambda: f"share_id={ref.share_id} 提取码={'有' if ref.passcode else '无'}")

        def _meta():
            meta.update(self.share_meta(ref))
            return (f"shareid={meta.get('shareid')} uk={meta.get('uk')} "
                    f"sekey={'有' if meta.get('sekey') else '无'}")
        self._run_step(steps, "分享信息 / 提取码校验", _meta)

        def _list():
            if not meta:
                raise ProviderError("上一步没有拿到 shareid，跳过")
            items, n = self._page_share(ref, meta, "/", 1)
            names = [str(x.get("server_filename")) for x in items[:5]]
            return f"根目录 {n} 项：{', '.join(names)}"
        self._run_step(steps, "列分享根目录（第一页）", _list)

        def _quota():
            q = self.quota()
            return f"总 {q['total'] // 1024 ** 3} GB / 已用 {q['used'] // 1024 ** 3} GB"
        self._run_step(steps, "查询我的容量", _quota)
        return steps
