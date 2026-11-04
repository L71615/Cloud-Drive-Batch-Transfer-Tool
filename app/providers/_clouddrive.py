# -*- coding: utf-8 -*-
"""云盘类接口的共享实现（夸克 / UC 同构）。

设计要点：
- 端点、参数、字段映射都来自 docs/spec/*.md，这里只做"把规格翻译成代码"。
- 子类只需要给 BASE / PR / REFERER / 链接正则。
- 目录树用 BFS 展开，限制深度与总数，避免循环分享把内存打满。
- 转存是异步的：save 返回 task_id，必须轮询 /task 到结束才算成功。
"""
from __future__ import annotations

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
                            # 转存时 pdir_fid 必须是这批文件在分享里的父目录，
                            # 一律传 "0"（根）会被服务端判成"token 校验异常"
                            "pdir": pdir,
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

    def _save_group(self, ref: ShareRef, stoken: str, pdir: str,
                    entries: list[Entry], dest: str) -> None:
        """一次 save 提交同一个父目录下的多个文件。"""
        d = self._api(self.P_SAVE, "POST", body={
            "fid_list": [e.meta.get("fid") for e in entries],
            "fid_token_list": [e.meta.get("token") for e in entries],
            "to_pdir_fid": dest,
            "pwd_id": ref.share_id,
            "stoken": stoken,
            "pdir_fid": pdir,
            "scene": "link",
        })
        task_id = d.get("task_id")
        if task_id:
            self._wait_task(str(task_id))

    def transfer_batch(self, ref: ShareRef, entries: list[Entry], target: str) -> list[str]:
        """批量转存：按"分享内的父目录"分组，一组一次 save。

        服务端要求 pdir_fid 与 fid_list 同源（同一批文件必须是同一个父目录的子项），
        否则会回 403「转存文件token校验异常」—— 之前全部传 "0" 就是这么挂的。
        返回与 entries 等长的目标路径列表，失败的位置是空串。
        """
        if not entries:
            return []
        dest = self.ensure_dir(target)
        groups: dict[str, list[int]] = {}
        for idx, e in enumerate(entries):
            groups.setdefault(str(e.meta.get("pdir") or "0"), []).append(idx)

        out = [""] * len(entries)
        first_err = ""
        for pdir, idxs in groups.items():
            group = [entries[i] for i in idxs]
            stoken = self.share_token(ref)
            try:
                self._save_group(ref, stoken, pdir, group, dest)
            except ProviderError as exc:
                # token 有有效期：换一次 stoken 再试，仍然不行就逐个提交，
                # 免得一个坏文件把整批 20 个都连坐。
                if "token" not in str(exc) and "stoken" not in str(exc):
                    first_err = first_err or str(exc)
                    continue
                self._stoken.pop(ref.share_id, None)
                try:
                    stoken = self.share_token(ref)
                    self._save_group(ref, stoken, pdir, group, dest)
                except ProviderError as exc2:
                    first_err = first_err or str(exc2)
                    for i, one in zip(idxs, group):
                        try:
                            self._save_group(ref, stoken, pdir, [one], dest)
                        except ProviderError:
                            continue
                        out[i] = f"{target.rstrip('/')}/{one.path.rsplit('/', 1)[-1]}"
                    continue
            for i, one in zip(idxs, group):
                out[i] = f"{target.rstrip('/')}/{one.path.rsplit('/', 1)[-1]}"
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
