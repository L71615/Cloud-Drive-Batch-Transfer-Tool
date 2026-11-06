# -*- coding: utf-8 -*-
"""本地假网盘服务：离线验证适配器的完整 HTTP 链路。

它只实现 docs/spec/*.md 里约定的字段子集，用来跑单元测试与手工联调，
不连接任何真实网盘、不包含任何真实接口密钥。

用法：
    python tools/mock_netdisk.py --port 8899
    # 百度：http://127.0.0.1:8899/baidu
    # 夸克/UC：http://127.0.0.1:8899/cd
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BAIDU_SHARE = {"1AbCdEf": {"shareid": "1001", "uk": "555", "pwd": "x9y8"}}
CD_SHARE = {"a1b2c3d4e5f6": {"pwd": ""}, "zyxwvu987654": {"pwd": "9a8b"}}

# 压测用的分享树：share_id -> {相对 fid: [条目]}。
# 真实服务端每个分享各有自己的目录树，这里也必须按分享隔离，
# 否则 200 条链接会共用同一个根，压测就没有意义了。
SHARE_TREES: dict[str, dict[str, list[dict]]] = {}


def tree_for(share_id: str) -> dict[str, list[dict]]:
    return SHARE_TREES.get(str(share_id or "")) or CD_TREE


def install_stress(n_shares: int, files_per_share: int) -> list[str]:
    """造 n_shares 条分享，每条 1 个根文件 + 1 个子目录（内含若干文件）。

    返回分享 id 列表，供压测脚本拼链接。
    """
    ids: list[str] = []
    for i in range(n_shares):
        sid = f"stress{i:04d}"
        root_fid = f"pr{i}"
        sub = f"d{i}"
        tree: dict[str, list[dict]] = {
            "0": [
                {"fid": f"rf{i}", "file_name": f"root-{i}.txt", "size": 100 + i,
                 "dir": False, "pdir_fid": root_fid},
                {"fid": sub, "file_name": f"目录{i}", "size": 0,
                 "dir": True, "pdir_fid": root_fid},
            ],
            sub: [],
        }
        for j in range(max(0, files_per_share - 1)):
            tree[sub].append({
                "fid": f"f{i}_{j}", "file_name": f"part-{i:04d}-{j:03d}.bin",
                "size": 1024 * (j + 1), "dir": False, "pdir_fid": sub,
            })
        SHARE_TREES[sid] = tree
        CD_SHARE[sid] = {"pwd": ""}
        ids.append(sid)
    return ids

BAIDU_TREE = {
    "/": [
        {"server_filename": "a.txt", "fs_id": 11, "size": 120, "md5": "m-a", "isdir": 0, "path": "/a.txt"},
        {"server_filename": "b.bin", "fs_id": 12, "size": 4096, "md5": "m-b", "isdir": 0, "path": "/b.bin"},
        {"server_filename": "相册", "fs_id": 13, "size": 0, "isdir": 1, "path": "/相册"},
    ],
    "/相册": [
        {"server_filename": "c.txt", "fs_id": 14, "size": 64, "md5": "m-c", "isdir": 0, "path": "/相册/c.txt"},
    ],
}

# 分享内的目录：key 是**列目录时用的相对 fid**（根为 "0"，子目录用目录自己的 fid），
# 而每一项另带 `pdir_fid` —— 它在真实服务端上就等于"该项真正的父目录 fid"，
# 两个值不是一回事：save 时 pdir_fid 必须传条目的 pdir_fid，传查询用的相对 fid
# 会被判成 403 token 校验异常。这里把两种值刻意做成不同，才能回归这个 bug。
CD_TREE = {
    "0": [
        {"fid": "f1", "file_name": "a.txt", "size": 120, "dir": False,
         "share_fid_token": "t1", "pdir_fid": "pr1"},
        {"fid": "d1", "file_name": "相册", "size": 0, "dir": True,
         "share_fid_token": "t2", "pdir_fid": "pr1"},
    ],
    "d1": [
        {"fid": "f2", "file_name": "c.txt", "size": 64, "dir": False,
         "share_fid_token": "t3", "pdir_fid": "d1"},
    ],
}


def install_long_name(share_id: str = "longname01", length: int = 300) -> str:
    """造一条含超长文件名的分享，用来验证"坏名字只影响它自己"。"""
    SHARE_TREES[share_id] = {
        "0": [
            {"fid": "ln1", "file_name": "ok.txt", "size": 10, "dir": False,
             "share_fid_token": "lnt1", "pdir_fid": "pr9"},
            {"fid": "ln2", "file_name": "长" * length + ".mp4", "size": 99, "dir": False,
             "share_fid_token": "lnt2", "pdir_fid": "pr9"},
        ],
    }
    CD_SHARE[share_id] = {"pwd": ""}
    return share_id


def _parent(p: str) -> str:
    p = (p or "/").rstrip("/") or "/"
    i = p.rfind("/")
    return "/" if i <= 0 else p[:i]


def _base(p: str) -> str:
    return (p or "").rstrip("/").rsplit("/", 1)[-1]


def _rel_path(tree: dict, item: dict) -> str:
    """条目在分享内的相对路径（跟着 pdir_fid 往上走），如 /相册/c.txt。"""
    parts: list[str] = []
    cur: dict | None = item
    seen: set[str] = set()
    while cur is not None:
        parts.append(str(cur.get("file_name") or ""))
        pf = str(cur.get("pdir_fid") or "0")
        if pf == "0" or pf in seen:
            break
        seen.add(pf)
        cur = next((x for items in tree.values() for x in items
                    if str(x.get("fid")) == pf), None)
    return "/" + "/".join(reversed([p for p in parts if p]))


class State:
    """一次进程内的"用户网盘"模型：够用来验证列目录 / 建目录 / 转存落盘。"""

    def __init__(self) -> None:
        self.mkdirs = 0
        self.saves: list[dict] = []
        self.transfers: list[dict] = []
        # 百度：绝对路径 -> {isdir,size,md5,fid}
        self.baidu: dict[str, dict] = {}
        # 夸克 / UC：绝对路径 <-> fid
        self.cd_dirs: dict[str, str] = {"/": "0"}
        self.cd_fid_dir: dict[str, str] = {"0": "/"}
        self.cd: dict[str, dict] = {}
        # 百度异步转存任务：taskid -> {"to": [...]}，第一次查询就完成
        self.baidu_tasks: dict[str, list] = {}
        self.seq = 0
        # share_fid_token：真实服务端只校验"归属 + 有效期"，可重复提交；
        # 这里签发后记住 owner，用来回归"pdir_fid 传错"和"token 串了"两类 bug
        self.issued: dict[str, str] = {}

    def new_fid(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq}"

    def issue_token(self, fid: str) -> str:
        self.seq += 1
        tok = f"{fid}-t{self.seq}"
        self.issued[tok] = fid
        if len(self.issued) > 5000:  # 别无限涨
            for k in list(self.issued)[:2000]:
                self.issued.pop(k, None)
        return tok


class Handler(BaseHTTPRequestHandler):
    state = State()

    def log_message(self, *args) -> None:  # 安静
        pass

    # ---------- 基础设施 ----------
    def _json(self, obj, code: int = 200) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        ctype = self.headers.get("Content-Type") or ""
        if "json" in ctype:
            try:
                return json.loads(raw or "{}")
            except json.JSONDecodeError:
                return {}
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def _cookie(self) -> str:
        return self.headers.get("Cookie") or ""

    def _guard(self, need: str) -> bool:
        cookie = self._cookie()
        if not cookie or "BAD" in cookie:
            self._json({"errno": -6, "status": 401, "message": "登录态失效"}, 401)
            return False
        if need == "baidu" and "BDUSS=" not in cookie:
            self._json({"errno": -6, "message": "缺少 BDUSS"}, 401)
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        p = u.path
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self._body()

        if p == "/baidu/share/verify":
            if not self._guard("baidu"):
                return
            surl = q.get("surl", "")
            share = BAIDU_SHARE.get(surl)
            if not share:
                return self._json({"errno": -62})
            if body.get("pwd") != share["pwd"]:
                return self._json({"errno": -9})
            return self._json({"errno": 0, "randsk": "sekey%2Babc"})

        if p == "/baidu/share/transfer":
            if not self._guard("baidu"):
                return
            self.state.transfers.append({"fsidlist": body.get("fsidlist"), "path": body.get("path")})
            try:
                fids = json.loads(body.get("fsidlist") or "[]")
            except json.JSONDecodeError:
                fids = []
            names = {str(x["fs_id"]): x["server_filename"]
                     for items in BAIDU_TREE.values() for x in items}
            dest = str(body.get("path") or "/").rstrip("/")
            to = [{"from": f, "to": f"{dest}/{names.get(str(f), 'unknown')}"} for f in fids]
            for f in fids:
                name = names.get(str(f), "unknown")
                path = f"{dest}/{name}"
                src = next((x for items in BAIDU_TREE.values() for x in items
                            if str(x["fs_id"]) == str(f)), {})
                self.state.baidu.setdefault(path, {
                    "isdir": False, "size": int(src.get("size") or 0),
                    "md5": str(src.get("md5") or ""), "fid": self.state.new_fid("bf"),
                })
            # 真实接口是异步的（async=1）：先回 taskid，再轮询 taskquery。
            # 这里也照这个形状回，第一次查询就给结果——让"没轮询"的实现跑不过测试。
            taskid = self.state.new_fid("task")
            self.state.baidu_tasks[taskid] = to
            return self._json({"errno": 0, "taskid": taskid, "extra": {"list": []}})

        if p == "/baidu/api/create":
            if not self._guard("baidu"):
                return
            path = str(body.get("path") or "/")
            seg = ""
            for part in [x for x in path.split("/") if x]:
                seg = seg + "/" + part
                self.state.baidu.setdefault(
                    seg, {"isdir": True, "size": 0, "fid": self.state.new_fid("bd")})
            self.state.mkdirs += 1
            return self._json({"errno": 0, "path": path})

        if p == "/cd/1/clouddrive/share/sharepage/token":
            if not self._guard("cd"):
                return
            share = CD_SHARE.get(str(body.get("pwd_id")))
            if not share:
                return self._json({"status": 41006, "code": 41006, "message": "分享不存在"})
            if share["pwd"] and body.get("passcode") != share["pwd"]:
                return self._json({"status": 41005, "code": 41005, "message": "提取码错误"})
            return self._json({"status": 0, "code": 0, "data": {"stoken": "st-1"}})

        if p == "/cd/1/clouddrive/share/sharepage/save":
            if not self._guard("cd"):
                return
            st = self.state
            dest_fid = str(body.get("to_pdir_fid") or "0")
            dpath = st.cd_fid_dir.get(dest_fid, "/")
            # 真实服务端会校验 fid 与 pdir_fid 同源、token 与 fid 配对：
            # 这里照做，否则"pdir_fid 传查询相对 fid"这种 bug 在自检里看不出来。
            # token 在真实服务端上可复用（只校验归属与有效期），所以不记 used。
            pdir = str(body.get("pdir_fid") or "0")
            tree = tree_for(body.get("pwd_id"))
            fids = [str(x) for x in (body.get("fid_list") or [])]
            tokens = [str(x) for x in (body.get("fid_token_list") or [])]
            for i, fid in enumerate(fids):
                got = tokens[i] if i < len(tokens) else ""
                src = next((x for items in tree.values() for x in items
                            if str(x.get("fid")) == fid), None)
                if src is None or str(src.get("pdir_fid") or "0") != pdir \
                        or st.issued.get(got) != fid:
                    return self._json(
                        {"status": 403, "code": 41020,
                         "message": f"转存文件token校验异常[{got} token校验异常]"}, 403)
            dst = []
            for fid in (body.get("fid_list") or []):
                src = next((x for items in tree.values() for x in items
                            if str(x.get("fid")) == str(fid)), {})
                name = str(src.get("file_name") or "unknown")
                # 真实 UC 会把分享内的目录结构原样重建在目标目录下
                # （/相册/c.txt → <目标>/相册/c.txt），这里照做，否则
                # "按完整路径判重"在自检里看不出问题。
                rel = _rel_path(tree, src).lstrip("/")
                base = dpath.rstrip("/")
                path = f"{base}/{rel}" if base else f"/{rel}"
                st.cd.setdefault(path, {
                    "size": int(src.get("size") or 0), "fid": st.new_fid("cf"),
                })
                parents = path.rsplit("/", 1)[0] or "/"
                if parents != "/" and parents not in st.cd_dirs.values():
                    st.cd_dirs.setdefault(parents, st.new_fid("cd"))
                    st.cd_fid_dir.setdefault(st.cd_dirs[parents], parents)
                dst.append({"path": path, "fid": st.cd[path]["fid"]})
            st.saves.append({**body, "dst": dst})
            return self._json({"status": 0, "code": 0, "data": {"task_id": "tk-1"}})

        if p == "/cd/1/clouddrive/file":
            if not self._guard("cd"):
                return
            st = self.state
            parent_fid = str(body.get("pdir_fid") or "0")
            name = str(body.get("file_name") or "新建文件夹")
            ppath = st.cd_fid_dir.get(parent_fid, "/")
            path = (ppath.rstrip("/") + "/" + name) if ppath != "/" else "/" + name
            fid = st.new_fid("d")
            st.cd_dirs[path] = fid
            st.cd_fid_dir[fid] = path
            st.mkdirs += 1
            return self._json({"status": 0, "code": 0, "data": {"fid": fid}})

        if p == "/cd/1/clouddrive/file/delete":
            if not self._guard("cd"):
                return
            st = self.state
            gone = []
            for fid in body.get("file_list") or []:
                for path, meta in list(st.cd.items()):
                    if str(meta.get("fid")) == str(fid):
                        st.cd.pop(path, None)
                        gone.append(path)
            return self._json({"status": 0, "code": 0, "data": {"task_id": "", "removed": gone}})

        self._json({"status": 404, "message": f"no route {p}"}, 404)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        p = u.path
        q = {k: v[0] for k, v in parse_qs(u.query).items()}

        if p == "/baidu/api/shorturlinfo":
            share = BAIDU_SHARE.get(q.get("shorturl", ""))
            if not share:
                return self._json({"errno": -62})
            return self._json({"errno": 0, "shareid": share["shareid"], "uk": share["uk"]})

        if p == "/baidu/share/taskquery":
            if not self._guard("baidu"):
                return
            taskid = str(q.get("taskid") or "")
            to = self.state.baidu_tasks.get(taskid)
            if to is None:
                return self._json({"errno": 31066})
            self.state.baidu_tasks.pop(taskid, None)
            return self._json({"errno": 0, "status": 2, "taskid": taskid, "extra": {"list": to}})

        if p == "/baidu/share/list":
            if not self._guard("baidu"):
                return
            items = BAIDU_TREE.get(q.get("dir") or "/", [])
            return self._json({"errno": 0, "list": items})

        if p == "/baidu/api/list":
            if not self._guard("baidu"):
                return
            d = (q.get("dir") or "/").rstrip("/") or "/"
            items = []
            for path, meta in sorted(self.state.baidu.items()):
                if _parent(path) != d:
                    continue
                items.append({
                    "server_filename": _base(path),
                    "isdir": 1 if meta.get("isdir") else 0,
                    "size": int(meta.get("size") or 0),
                    "md5": str(meta.get("md5") or ""),
                    "fs_id": meta.get("fid"),
                })
            return self._json({"errno": 0, "list": items})

        if p == "/baidu/api/quota":
            if not self._guard("baidu"):
                return
            return self._json({"errno": 0, "total": 10 * 1024 ** 3, "used": 1024 ** 3})

        if p == "/cd/1/clouddrive/share/sharepage/detail":
            if not self._guard("cd"):
                return
            if q.get("stoken") != "st-1":
                return self._json({"status": 41006, "code": 41006, "message": "stoken 无效"})
            fid = str(q.get("pdir_fid") or "0")
            tree = tree_for(q.get("pwd_id"))
            if fid not in tree:
                # 真实服务端：拿条目自己的 pdir_fid 来查 detail 会回 41038，
                # 查 detail 只能用列表里的相对 fid（根是 "0"）
                return self._json({"status": 404, "code": 41038,
                                   "message": f"文件没有被分享:[{{{fid}}}]"})
            items = []
            for x in tree.get(fid, []):
                one = dict(x)
                # 每次 detail 都发新 token；旧 token 仍可复用（真实行为），
                # 但"归属必须对得上"这一点保持强校验
                one["share_fid_token"] = self.state.issue_token(str(x.get("fid")))
                items.append(one)
            return self._json({"status": 0, "code": 0,
                               "data": {"list": items, "metadata": {"_total": len(items)}}})

        if p == "/cd/1/clouddrive/file/sort":
            if not self._guard("cd"):
                return
            fid = str(q.get("pdir_fid") or "0")
            path = self.state.cd_fid_dir.get(fid, "/")
            items = []
            for dpath, dfid in sorted(self.state.cd_dirs.items()):
                if dpath != "/" and _parent(dpath) == path:
                    items.append({"fid": dfid, "file_name": _base(dpath), "size": 0, "dir": True})
            for fpath, meta in sorted(self.state.cd.items()):
                if _parent(fpath) == path:
                    items.append({"fid": meta["fid"], "file_name": _base(fpath),
                                  "size": int(meta.get("size") or 0), "dir": False})
            return self._json({"status": 0, "code": 0,
                               "data": {"list": items, "metadata": {"_total": len(items)}}})

        if p == "/cd/1/clouddrive/task":
            if not self._guard("cd"):
                return
            return self._json({"status": 0, "code": 0, "data": {"status": 2, "task_id": q.get("task_id")}})

        if p == "/cd/1/clouddrive/member":
            if not self._guard("cd"):
                return
            return self._json({"status": 0, "code": 0, "data": {
                "total_capacity": 10 * 1024 ** 3, "use_capacity": 1024 ** 3}})

        self._json({"status": 404, "message": f"no route {p}"}, 404)


def serve(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"mock netdisk on http://127.0.0.1:{a.port}/baidu  and  /cd")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
