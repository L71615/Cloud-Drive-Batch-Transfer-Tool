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

CD_TREE = {
    "0": [
        {"fid": "f1", "file_name": "a.txt", "size": 120, "dir": False, "share_fid_token": "t1"},
        {"fid": "d1", "file_name": "相册", "size": 0, "dir": True, "share_fid_token": "t2"},
    ],
    "d1": [
        {"fid": "f2", "file_name": "c.txt", "size": 64, "dir": False, "share_fid_token": "t3"},
    ],
}


def _parent(p: str) -> str:
    p = (p or "/").rstrip("/") or "/"
    i = p.rfind("/")
    return "/" if i <= 0 else p[:i]


def _base(p: str) -> str:
    return (p or "").rstrip("/").rsplit("/", 1)[-1]


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
        self.seq = 0

    def new_fid(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq}"


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
            return self._json({"errno": 0, "extra": {"list": to}})

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
            # 真实服务端会校验 fid 与 pdir_fid 同源、token 与 fid 配对，
            # 这里照做，否则"pdir_fid 恒为 0"这种 bug 在自检里看不出来
            pdir = str(body.get("pdir_fid") or "0")
            allowed = {str(x.get("fid")): str(x.get("share_fid_token") or "")
                       for x in CD_TREE.get(pdir, [])}
            fids = [str(x) for x in (body.get("fid_list") or [])]
            tokens = [str(x) for x in (body.get("fid_token_list") or [])]
            for i, fid in enumerate(fids):
                want = allowed.get(fid)
                got = tokens[i] if i < len(tokens) else ""
                if want is None or want != got:
                    return self._json(
                        {"status": 403, "code": 403,
                         "message": f"转存文件token校验异常[{got} token校验异常]"}, 403)
            dst = []
            for fid in (body.get("fid_list") or []):
                src = next((x for items in CD_TREE.values() for x in items
                            if str(x.get("fid")) == str(fid)), {})
                name = str(src.get("file_name") or "unknown")
                path = (dpath.rstrip("/") + "/" + name) if dpath != "/" else "/" + name
                st.cd.setdefault(path, {
                    "size": int(src.get("size") or 0), "fid": st.new_fid("cf"),
                })
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
            fid = q.get("pdir_fid") or "0"
            items = CD_TREE.get(fid, [])
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
