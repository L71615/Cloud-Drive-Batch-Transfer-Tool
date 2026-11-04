# -*- coding: utf-8 -*-
"""云盘类接口的共享实现（夸克 / UC 同构）。

设计要点：
- 端点、参数、字段映射都来自 docs/spec/*.md，这里只做"把规格翻译成代码"。
- 子类只需要给 BASE / PR / REFERER / 链接正则。
- 目录树用 BFS 展开，限制深度与总数，避免循环分享把内存打满。
- 转存是异步的：save 返回 task_id，必须轮询 /task 到结束才算成功。
"""
from __future__ import annotations

import os
import time

from ._http import Client
from .base import Entry, Provider, ProviderError, ProviderNotReady, ShareRef

MAX_DEPTH = 8
MAX_ENTRIES = 5000

ERR_OK = (0, 200, None)

# 云盘类接口的常见业务码 → 人话（写进任务错误，界面直接可读）
ERR_TEXT = {
    31001: "登录态已失效，请重新登录",
    31023: "请求参数被拒绝",
    31066: "文件数量超出限制",
    41004: "提取码错误",
    41005: "该分享需要提取码",
    41006: "分享不存在或已取消",
    41008: "转存数量超出限制",
    41020: "该分享不允许转存",
}


class CloudDrive(Provider):
    """夸克/UC 的通用实现。"""

    BASE = ""
    PR = "ucpro"
    FR = "pc"
    REFERER = ""

    # 端点（相对 BASE），子类可覆盖
    P_TOKEN = "/1/clouddrive/share/sharepage/token"
    P_DETAIL = "/1/clouddrive/share/sharepage/detail"
    P_SAVE = "/1/clouddrive/share/sharepage/save"
    P_TASK = "/1/clouddrive/task"
    P_SORT = "/1/clouddrive/file/sort"
    P_FILE = "/1/clouddrive/file"
    P_MEMBER = "/1/clouddrive/member"

    def __init__(self, credential: str = ""):
        super().__init__(credential)
        self._client: Client | None = None
        self._stoken: dict[str, str] = {}
        self._dirs: dict[str, str] = {}

    # ---------------- 基础设施 ----------------
    @property
    def ready(self) -> bool:
        return bool(self.BASE)

    def _c(self) -> Client:
        if not self.ready:
            raise ProviderNotReady(f"{self.label}：端点未配置")
        if self._client is None:
            self._client = Client(self.name, self.credential, self.qps, referer=self.REFERER)
        return self._client

    def _api(self, path: str, method: str = "GET", params: dict | None = None,
             body: dict | None = None) -> dict:
        q = {"pr": self.PR, "fr": self.FR}
        if params:
            q.update({k: v for k, v in params.items() if v not in (None, "")})
        data = self._c().request(method, self.BASE + path, params=q, body=body)
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name}: 响应不是 JSON")
        status = data.get("status")
        if status not in ERR_OK:
            code = data.get("code")
            raw = data.get("message") or ""
            # 服务端的中文提示已经足够具体，优先用它；没有才退回本地码表
            msg = raw or ERR_TEXT.get(code) or "接口返回错误"
            if code in (31001, 31023, 401) or status in (401, -1):
                from .base import AuthExpired
                raise AuthExpired(f"{self.name}: {msg}（code={code}）")
            raise ProviderError(f"{self.name}: {msg}（status={status} code={code}）")
        return data.get("data") or {}

    # ---------------- 分享 ----------------
    def share_token(self, ref: ShareRef) -> str:
        if ref.share_id in self._stoken:
            return self._stoken[ref.share_id]
        d = self._api(self.P_TOKEN, "POST", body={
            "pwd_id": ref.share_id,
            "passcode": ref.passcode or "",
        })
        stoken = d.get("stoken")
        if not stoken:
            raise ProviderError("没能拿到 stoken：提取码错误或分享已失效")
        self._stoken[ref.share_id] = stoken
        return stoken

    def _page_share(self, ref: ShareRef, stoken: str, pdir: str, page: int, size: int = 100) -> dict:
        return self._api(self.P_DETAIL, params={
            "pwd_id": ref.share_id, "stoken": stoken, "pdir_fid": pdir, "force": "0",
            "_page": page, "_size": size,
            "_fetch_banner": "0", "_fetch_share": "0", "_fetch_total": "1",
            "_sort": "file_type:asc,file_name:asc",
        })

    def list_share(self, ref: ShareRef) -> list[Entry]:
        stoken = self.share_token(ref)
        count = 0
        queue: list[tuple[str, str, int]] = [("0", "", 0)]
        while queue and count < MAX_ENTRIES:
            pdir, prefix, depth = queue.pop(0)
            if depth > MAX_DEPTH:
                continue
            page = 1
            size = 100
            seen_fids: set[str] = set()
            while True:
                d = self._page_share(ref, stoken, pdir, page, size)
                items = d.get("list") or []
                # 有些分享接口忽略 _page 参数，同一页会被反复返回：用 fid 集合识别并跳出
                fids = {str(it.get("fid") or "") for it in items}
                if fids and fids <= seen_fids:
                    break
                seen_fids |= fids
                for it in items:
                    name = str(it.get("file_name") or "")
                    path = f"{prefix}/{name}" if prefix else f"/{name}"
                    if it.get("dir"):
                        queue.append((str(it.get("fid")), path, depth + 1))
                        continue
                    yield Entry(
                        path=path,
                        size=int(it.get("size") or 0),
                        digest=str(it.get("fid") or ""),
                        meta={
                            "fid": it.get("fid"),
                            "token": it.get("share_fid_token"),
                            # 转存时 pdir_fid 要的是这一项**自己的父目录 fid**（pdir_fid 字段），
                            # 不是列目录时用的相对 fid（根是 "0"）——传错了就回 403 token 校验异常
                            "pdir": str(it.get("pdir_fid") or pdir),
                            # 相对 fid 留着：重新取 token 时还得用它查 detail
                            "pdir_query": pdir,
                        },
                    )
                    count += 1
                    if count >= MAX_ENTRIES:
                        break
                total = int((d.get("metadata") or {}).get("_total") or 0)
                if not total or page * size >= total or not items:
                    break
                page += 1
        return

    # ---------------- 我的目录 ----------------
    def _children(self, pdir: str, size: int = 200) -> list[dict]:
        d = self._api(self.P_SORT, params={
            "pdir_fid": pdir, "_page": 1, "_size": size,
            "_fetch_total": "1", "_sort": "file_type:asc,file_name:asc",
        })
        return d.get("list") or []

    def ensure_dir(self, path: str) -> str:
        """把 /a/b/c 逐级创建出来，返回最终目录 fid。"""
        fid = "0"
        for seg in [s for s in str(path or "").split("/") if s]:
            key = f"{fid}/{seg}"
            if key in self._dirs:
                fid = self._dirs[key]
                continue
            found = ""
            for ch in self._children(fid):
                if ch.get("dir") and str(ch.get("file_name")) == seg:
                    found = str(ch.get("fid"))
                    break
            if not found:
                d = self._api(self.P_FILE, "POST", body={
                    "pdir_fid": fid, "file_name": seg,
                    "dir_path": "", "dir_init_lock": False,
                })
                found = str(d.get("fid") or "")
                if not found:
                    raise ProviderError(f"创建目录失败：{seg}")
            self._dirs[key] = found
            fid = found
        return fid

    # ---------------- 转存 ----------------
    def transfer(self, ref: ShareRef, entry: Entry, target: str) -> str:
        res = self.transfer_batch(ref, [entry], target)
        if not res or not res[0]:
            raise ProviderError("转存失败")
        return res[0]

    def _fresh_tokens(self, ref: ShareRef, stoken: str, query: str,
                      wanted: set[str], size: int = 100) -> dict[str, str]:
        """重新取一批 share_fid_token。

        token 会过期：目录树是边遍历边转存的，大分享里前面目录的 token 等到
        flush 时可能已经失效，服务端就回 403「转存文件token校验异常」。
        注意 `query` 必须是**列目录时用的相对 fid**（根是 "0"），
        用文件自己的 pdir_fid 去查会回 41038「文件没有被分享」。
        """
        out: dict[str, str] = {}
        page = 1
        while True:
            d = self._page_share(ref, stoken, query, page, size)
            items = d.get("list") or []
            for it in items:
                fid = str(it.get("fid") or "")
                if fid in wanted and fid not in out:
                    out[fid] = str(it.get("share_fid_token") or "")
            total = int((d.get("metadata") or {}).get("_total") or 0)
            if len(out) >= len(wanted) or not items or (total and page * size >= total):
                break
            page += 1
        return out

    @staticmethod
    def _is_token_error(exc: Exception) -> bool:
        s = str(exc)
        return "token" in s or "41020" in s or "校验" in s

    def _save_group(self, ref: ShareRef, stoken: str, pdir: str,
                    entries: list[Entry], dest: str,
                    tokens: list[str] | None = None,
                    query: str | None = None) -> None:
        """一次 save 提交同一个父目录下的多个文件。

        `pdir` 必须是文件在分享里的**真实父目录 fid**（取自条目的 pdir_fid），
        传查询用的相对 fid（根的 "0"）会被判成 token 校验异常。
        `tokens` 为空时按 `query`（列目录用的相对 fid）重新取一遍
        —— 条目携带的 token 可能已经过期。
        """
        fids = [str(e.meta.get("fid") or "") for e in entries]
        if tokens is None:
            tokens = [str(e.meta.get("token") or "") for e in entries]
            if query is not None:
                try:
                    fresh = self._fresh_tokens(ref, stoken, query, set(fids))
                    tokens = [fresh.get(fid) or old for fid, old in zip(fids, tokens)]
                except ProviderError:
                    pass  # 取不到就用条目自带的，失败了还有下面的重试路径
        d = self._api(self.P_SAVE, "POST", body={
            "fid_list": fids,
            "fid_token_list": tokens,
            "to_pdir_fid": dest,
            "pwd_id": ref.share_id,
            "stoken": stoken,
            "pdir_fid": pdir,
            "scene": "link",
        })
        task_id = d.get("task_id")
        if task_id:
            self._wait_task(str(task_id))

    def _locate(self, target: str, names: list[str]) -> dict[str, str]:
        """转存后核对真实落点：返回 {文件名: 实际路径}，最多往下看两层。"""
        base = target.rstrip("/") or "/"
        want = set(names)
        found: dict[str, str] = {}
        try:
            top = self.browse(base)
        except ProviderError:
            return found
        for it in top:
            if not it["is_dir"] and it["name"] in want:
                found.setdefault(it["name"], it["path"])
        if len(found) >= len(want):
            return found
        for it in top:
            if not it["is_dir"]:
                continue
            try:
                sub = self.browse(it["path"])
            except ProviderError:
                continue
            for s in sub:
                if not s["is_dir"] and s["name"] in want:
                    found.setdefault(s["name"], s["path"])
            if len(found) >= len(want):
                break
        return found

    def transfer_batch(self, ref: ShareRef, entries: list[Entry], target: str) -> list[str]:
        """批量转存：按「分享内的真实父目录」分组，一组一次 save。

        失败处理：遇到 token 类错误（过期/校验异常）时，刷新 stoken 并重新取
        token 整组重试一次；仍然不行就逐个文件重取 token 单独提交，
        免得一个坏文件把整批 20 个连坐。
        返回与 entries 等长的目标路径列表，失败的位置是空串。
        """
        errs: list[str] = [""] * len(entries)
        self.last_batch_errors = errs
        if not entries:
            return []
        dest = self.ensure_dir(target)
        groups: dict[tuple[str, str], list[int]] = {}
        for idx, e in enumerate(entries):
            key = (str(e.meta.get("pdir") or "0"), str(e.meta.get("pdir_query") or "0"))
            groups.setdefault(key, []).append(idx)

        out = [""] * len(entries)
        done_idx: list[int] = []
        first_err = ""
        for (pdir, query), idxs in groups.items():
            group = [entries[i] for i in idxs]
            stoken = self.share_token(ref)
            try:
                self._save_group(ref, stoken, pdir, group, dest, query=query)
                done_idx.extend(idxs)
            except ProviderError as exc:
                if not self._is_token_error(exc):
                    first_err = first_err or str(exc)
                    for i in idxs:
                        errs[i] = str(exc)
                    continue
                self._stoken.pop(ref.share_id, None)
                try:
                    stoken = self.share_token(ref)
                    fresh = self._fresh_tokens(
                        ref, stoken, query, {str(e.meta.get("fid")) for e in group})
                    self._save_group(
                        ref, stoken, pdir, group, dest,
                        tokens=[fresh.get(str(e.meta.get("fid"))) or str(e.meta.get("token") or "")
                                for e in group])
                    done_idx.extend(idxs)
                except ProviderError as exc2:
                    first_err = first_err or str(exc2)
                    for i, one in zip(idxs, group):
                        fid = str(one.meta.get("fid"))
                        q = str(one.meta.get("pdir_query") or query)
                        try:
                            stoken = self.share_token(ref)
                            fresh = self._fresh_tokens(ref, stoken, q, {fid})
                            self._save_group(
                                ref, stoken, pdir, [one], dest,
                                tokens=[fresh.get(fid) or str(one.meta.get("token") or "")])
                        except ProviderError as exc3:
                            errs[i] = str(exc3)
                            continue
                        done_idx.append(i)
        if done_idx:
            # 落点可以算出来，不必每次列目录：UC 会把分享内的目录结构原样重建在
            # 目标目录下（实测 /3184/解压码.7z → <目标>/3184/解压码.7z），
            # 所以 dst = 目标 + 条目在分享内的路径。
            # 之前每批都 _locate() 扫一遍目标目录（含所有子目录），文件一多就变成
            # O(文件数²) 的请求量，20 个文件的一批要十几秒，看着就像"转存不完了"。
            base = target.rstrip("/") or ""
            for i in done_idx:
                out[i] = f"{base}{entries[i].path}"
            if os.environ.get("PANTRANSFER_VERIFY_LOCATE") == "1":
                names = [entries[i].path.rsplit("/", 1)[-1] for i in done_idx]
                landed = self._locate(target, names)
                for i in done_idx:
                    name = entries[i].path.rsplit("/", 1)[-1]
                    if landed.get(name):
                        out[i] = landed[name]
        if not any(out) and first_err:
            raise ProviderError(first_err)
        return out

    def _wait_task(self, task_id: str, timeout: float = 120.0) -> None:
        end = time.time() + timeout
        time.sleep(0.2)
        while time.time() < end:
            d = self._api(self.P_TASK, params={"task_id": task_id, "retry_index": "0"})
            status = d.get("status")
            if status == 2:
                return
            if status in (3, 4):
                raise ProviderError(f"转存任务失败：{d.get('message') or status}")
            time.sleep(0.5)
        raise ProviderError("转存任务超时（120s）")

    # ---------------- 其它 ----------------
    def _fid_for(self, path: str) -> str:
        """把绝对路径解析成 fid；不创建任何目录。"""
        fid = "0"
        for seg in [s for s in str(path or "").split("/") if s]:
            key = f"{fid}/{seg}"
            if key in self._dirs:
                fid = self._dirs[key]
                continue
            found = ""
            for ch in self._children(fid):
                if ch.get("dir") and str(ch.get("file_name")) == seg:
                    found = str(ch.get("fid"))
                    break
            if not found:
                raise ProviderError(f"目录不存在：{path}")
            self._dirs[key] = found
            fid = found
        return fid

    def browse(self, path: str = "/") -> list[dict]:
        fid = self._fid_for(path or "/")
        base = str(path or "/").rstrip("/")
        out = []
        for ch in self._children(fid):
            name = str(ch.get("file_name") or "")
            out.append({
                "name": name,
                "path": f"{base}/{name}",
                "is_dir": bool(ch.get("dir")),
                "size": int(ch.get("size") or 0),
                "meta": {"fid": ch.get("fid")},
            })
        return out

    def list_dir(self, path: str = "0") -> list[Entry]:
        fid = path if path == "0" or path.isdigit() else self.ensure_dir(path)
        return [
            Entry(
                path=f"/{ch.get('file_name')}",
                size=int(ch.get("size") or 0),
                digest=str(ch.get("fid") or ""),
                meta={"fid": ch.get("fid"), "dir": bool(ch.get("dir"))},
            )
            for ch in self._children(fid)
        ]

    def quota(self) -> dict:
        d = self._api(self.P_MEMBER)
        total = int(d.get("total_capacity") or 0)
        used = int(d.get("use_capacity") or 0)
        return {"total": total, "used": used, "free": max(0, total - used)}

    def check_auth(self) -> dict:
        try:
            self._children("0", size=1)
        except ProviderNotReady as e:
            return {"valid": False, "message": str(e)}
        return {"valid": True, "message": "登录态有效"}

    def diagnose(self, ref: ShareRef) -> list[dict]:
        steps: list[dict] = []
        stoken: dict[str, str] = {}
        self._run_step(steps, "解析链接", lambda: f"share_id={ref.share_id} 提取码={'有' if ref.passcode else '无'}")

        def _token():
            stoken["v"] = self.share_token(ref)
            return f"stoken={stoken['v'][:8]}…（长度 {len(stoken['v'])}）"
        self._run_step(steps, "分享 token", _token)

        def _list():
            if not stoken:
                raise ProviderError("上一步没有拿到 stoken，跳过")
            d = self._page_share(ref, stoken["v"], "0", 1)
            items = d.get("list") or []
            total = int((d.get("metadata") or {}).get("_total") or 0)
            names = [str(x.get("file_name")) for x in items[:5]]
            return f"根目录 共 {total} 项，本页 {len(items)}：{', '.join(names)}"
        self._run_step(steps, "列分享根目录（第一页）", _list)

        def _quota():
            q = self.quota()
            return f"总 {q['total'] // 1024 ** 3} GB / 已用 {q['used'] // 1024 ** 3} GB"
        self._run_step(steps, "查询我的容量", _quota)
        return steps
