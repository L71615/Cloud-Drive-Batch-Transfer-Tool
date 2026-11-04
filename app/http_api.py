# -*- coding: utf-8 -*-
"""HTTP JSON API + 内置单页 UI。只用标准库，绑定 127.0.0.1。

路由表
  GET  /                          内置页面
  GET  /api/health                版本、provider 能力
  GET  /api/providers
  GET  /api/accounts              已保存的登录态
  POST /api/accounts              {provider, name, credential}
  DEL  /api/accounts/{id}
  POST /api/parse                 {provider, text} -> 链接预览（不联网）
  GET  /api/tasks?limit=100
  POST /api/tasks                 {provider, links, target, credential?, account_id?}
  GET  /api/tasks/{id}
  POST /api/tasks/{id}/pause|resume|cancel
"""
from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import __version__
from .browser_login import MANAGER as LOGIN_MANAGER
from .browser_login import BrowserLoginError
from .credentials import normalize, validate
from .db import Store, now
from .providers import create, names
from .providers._http import trace_off, trace_on
from .providers.base import AuthExpired, ProviderError, ProviderNotReady
from .tasks import TaskRunner

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TASK_ACTION = re.compile(r"^/api/tasks/([0-9a-zA-Z]+)/(pause|resume|cancel|retry)$")
TASK_ONE = re.compile(r"^/api/tasks/([0-9a-zA-Z]+)$")
ACCOUNT_ONE = re.compile(r"^/api/accounts/(\d+)$")
ACCOUNT_CHECK = re.compile(r"^/api/accounts/(\d+)/check$")
LOGIN_ONE = re.compile(r"^/api/login/browser/([0-9a-zA-Z]+)$")


class App:
    def __init__(self, db_path: str, workers: int = 2):
        self.store = Store(db_path)
        self.runner = TaskRunner(self.store, workers=workers)
        self.runner.recover_orphans()

    def close(self) -> None:
        self.runner.shutdown()
        LOGIN_MANAGER.close_all()
        self.store.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "PanTransfer/" + __version__
    app: App = None  # type: ignore

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):  # 安静一点
        if os.environ.get("PANTRANSFER_DEBUG"):
            super().log_message(fmt, *args)

    def _send(self, code: int, payload=None, ctype="application/json; charset=utf-8", raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(
            payload, ensure_ascii=False, default=str
        ).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, message: str):
        self._send(code, {"ok": False, "error": message})

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 8 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # ---------- 路由 ----------
    def do_GET(self):
        try:
            self._route_get()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._err(500, f"{type(e).__name__}: {e}")

    def do_POST(self):
        try:
            self._route_post()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._err(500, f"{type(e).__name__}: {e}")

    def do_DELETE(self):
        try:
            u = urlparse(self.path)
            m = ACCOUNT_ONE.match(u.path)
            if m:
                self.app.store.run("DELETE FROM accounts WHERE id=?", (int(m.group(1)),))
                return self._send(200, {"ok": True})
            m = TASK_ONE.match(u.path)
            if m:
                self.app.runner.delete(m.group(1))
                return self._send(200, {"ok": True})
            m = LOGIN_ONE.match(u.path)
            if m:
                LOGIN_MANAGER.close(m.group(1))
                return self._send(200, {"ok": True})
            self._err(404, "not found")
        except Exception as e:  # noqa: BLE001
            self._err(500, f"{type(e).__name__}: {e}")

    def _route_get(self):
        u = urlparse(self.path)
        path = u.path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/api/health":
            return self._send(200, {
                "ok": True,
                "version": __version__,
                "providers": self._provider_caps(),
            })
        if path == "/api/providers":
            return self._send(200, {"ok": True, "providers": self._provider_caps()})
        if path == "/api/meta":
            return self._send(200, {
                "ok": True,
                "version": __version__,
                "providers": self._provider_caps(),
            })
        if path == "/api/stats":
            return self._send(200, {"ok": True, **self.app.runner.stats()})
        if path == "/api/settings":
            return self._send(200, {"ok": True, "settings": self.app.runner.settings()})
        if path == "/api/browse":
            q = parse_qs(u.query)
            provider = (q.get("provider") or [""])[0]
            target = (q.get("path") or ["/"])[0]
            account_id = (q.get("account_id") or [""])[0]
            cred = normalize((q.get("credential") or [""])[0])
            if provider not in names():
                return self._err(400, f"unknown provider: {provider}")
            if not cred and account_id:
                row = self.app.store.one(
                    "SELECT credential FROM accounts WHERE id=? AND provider=?",
                    (int(account_id), provider),
                )
                cred = (row or {}).get("credential", "")
            try:
                prov = create(provider, cred)
                prov.qps = max(prov.qps, 1.0)
                items = prov.browse(target)
            except ProviderNotReady as e:
                return self._err(400, str(e))
            except (ProviderError, AuthExpired) as e:
                return self._err(400, f"读取目录失败：{e}")
            return self._send(200, {"ok": True, "path": target, "items": items})
        if path == "/api/accounts":
            rows = self.app.store.all(
                "SELECT id,provider,name,created_at FROM accounts ORDER BY id DESC"
            )
            return self._send(200, {"ok": True, "accounts": rows})
        m = LOGIN_ONE.match(path)
        if m:
            sess = LOGIN_MANAGER.get(m.group(1))
            if not sess:
                return self._err(404, "登录会话不存在或已关闭")
            return self._send(200, {"ok": True, "login": sess.poll()})
        if path == "/api/tasks":
            limit = min(int(parse_qs(u.query).get("limit", ["100"])[0]), 500)
            rows = self.app.store.all(
                "SELECT id,provider,target,status,total,done,failed,skipped,error,"
                " links<>'' AS has_links, created_at,updated_at"
                " FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return self._send(200, {"ok": True, "tasks": rows})
        m = TASK_ONE.match(path)
        if m:
            view = self.app.runner.view(m.group(1))
            if not view:
                return self._err(404, "task not found")
            return self._send(200, {"ok": True, "task": view})
        return self._err(404, "not found")

    def _route_post(self):
        u = urlparse(self.path)
        path = u.path
        body = self._body()

        if path == "/api/settings":
            return self._send(200, {"ok": True, "settings": self.app.runner.update_settings(body)})

        if path == "/api/login/browser":
            provider = str(body.get("provider") or "")
            try:
                view = LOGIN_MANAGER.start(provider)
            except BrowserLoginError as e:
                return self._err(400, str(e))
            return self._send(200, {"ok": True, "login": view,
                                    "hint": "浏览器窗口已打开，登录完成后这里会自动捕获登录态"})

        if path == "/api/login/browser/save":
            sid = str(body.get("sid") or "")
            sess = LOGIN_MANAGER.get(sid)
            if not sess:
                return self._err(404, "登录会话不存在或已关闭")
            view = sess.poll()
            if view["status"] != "ready":
                return self._err(400, view["message"] or "尚未捕获到登录态")
            name = str(body.get("name") or "").strip() or f"{view['provider']}-扫码"
            self.app.store.run(
                "INSERT INTO accounts(provider,name,credential,created_at) VALUES(?,?,?,?)",
                (view["provider"], name, view["cookie"], now()),
            )
            row = self.app.store.one("SELECT id FROM accounts ORDER BY id DESC LIMIT 1") or {}
            LOGIN_MANAGER.close(sid)
            return self._send(200, {"ok": True, "id": row.get("id"), "count": view["count"]})

        m = ACCOUNT_CHECK.match(path)
        if m:
            row = self.app.store.one(
                "SELECT provider,credential FROM accounts WHERE id=?", (int(m.group(1)),)
            )
            if not row:
                return self._err(404, "account not found")
            try:
                prov = create(row["provider"], row["credential"])
                result = prov.check_auth()
            except ProviderNotReady as e:
                result = {"valid": False, "message": str(e)}
            except (ProviderError, AuthExpired) as e:
                result = {"valid": False, "message": f"请求失败：{e}"}
            except Exception as e:  # noqa: BLE001
                result = {"valid": False, "message": f"{type(e).__name__}: {e}"}
            return self._send(200, {"ok": True, **result})

        if path == "/api/diag":
            provider = str(body.get("provider") or "")
            if provider not in names():
                return self._err(400, f"unknown provider: {provider}")
            cred = normalize(str(body.get("credential") or ""))
            if not cred and body.get("account_id"):
                row = self.app.store.one(
                    "SELECT credential FROM accounts WHERE id=? AND provider=?",
                    (int(body["account_id"]), provider),
                )
                cred = (row or {}).get("credential", "")
            link = str(body.get("link") or "").strip()
            prov = create(provider, cred)
            prov.qps = max(prov.qps, 1.0)
            refs = prov.parse_links(link)
            if not refs:
                return self._send(200, {
                    "ok": True,
                    "steps": [{"name": "解析链接", "ok": False, "ms": 0,
                               "error": "没有识别到分享链接（检查链接格式）"}],
                    "raw": [],
                })
            ref = refs[0]
            if str(body.get("passcode") or "").strip():
                ref.passcode = str(body["passcode"]).strip()
            sink = trace_on()
            try:
                steps = prov.diagnose(ref)
            finally:
                trace_off()
            return self._send(200, {"ok": True, "steps": steps, "raw": sink})

        if path == "/api/accounts":
            provider = str(body.get("provider") or "")
            if provider not in names():
                return self._err(400, f"unknown provider: {provider}")
            v = validate(provider, str(body.get("credential") or ""))
            if not v["ok"]:
                return self._err(400, v["error"])
            cred = v["cookie"]
            self.app.store.run(
                "INSERT INTO accounts(provider,name,credential,created_at) VALUES(?,?,?,?)",
                (provider, str(body.get("name") or provider), cred, now()),
            )
            row = self.app.store.one("SELECT id FROM accounts ORDER BY id DESC LIMIT 1") or {}
            return self._send(200, {"ok": True, "id": row.get("id")})

        if path == "/api/accounts/validate":
            provider = str(body.get("provider") or "")
            if provider not in names():
                return self._err(400, f"unknown provider: {provider}")
            v = validate(provider, str(body.get("credential") or ""))
            return self._send(200, {
                "ok": True,
                "valid": v["ok"],
                "message": v["error"] or "格式正确，可以保存",
                "missing": v["missing"],
                "cookie": v["cookie"],
                "count": v.get("count", 0),
            })

        if path == "/api/parse":
            provider = str(body.get("provider") or "")
            text = str(body.get("text") or "")
            try:
                prov = create(provider, "")
            except KeyError:
                return self._err(400, f"unknown provider: {provider}")
            refs = prov.parse_links(text)
            # 选错网盘是最常见的原因：这里顺手看别的网盘能不能认出来，直接告诉用户
            hint = ""
            if not refs:
                for other in names():
                    if other == provider or other == "mock":
                        continue
                    try:
                        other_refs = create(other, "").parse_links(text)
                    except Exception:  # noqa: BLE001
                        other_refs = []
                    if other_refs:
                        label = create(other, "").label
                        hint = (f"这看起来是「{label}」的链接，"
                                f"请把上方的网盘切换成{label}再试")
                        break
            return self._send(200, {
                "ok": True,
                "count": len(refs),
                "refs": [r.__dict__ for r in refs],
                "hint": hint,
            })

        if path == "/api/tasks":
            provider = str(body.get("provider") or "")
            links = str(body.get("links") or "").strip()
            target = str(body.get("target") or "/PanTransfer").strip() or "/PanTransfer"
            if provider not in names():
                return self._err(400, f"unknown provider: {provider}")
            if not links:
                return self._err(400, "链接不能为空")
            cred = normalize(str(body.get("credential") or ""))
            if not cred and body.get("account_id"):
                row = self.app.store.one(
                    "SELECT credential FROM accounts WHERE id=? AND provider=?",
                    (int(body["account_id"]), provider),
                )
                cred = (row or {}).get("credential", "")
            task_id = self.app.runner.create_task(provider, links, target, cred)
            return self._send(200, {"ok": True, "id": task_id})

        m = TASK_ACTION.match(path)
        if m:
            task_id, action = m.group(1), m.group(2)
            if not self.app.store.one("SELECT id FROM tasks WHERE id=?", (task_id,)):
                return self._err(404, "task not found")
            if action == "retry":
                # 旧版本建的任务没有把链接落盘：允许重试时把链接补回来
                links = str(body.get("links") or "").strip()
                if links:
                    self.app.store.run(
                        "UPDATE tasks SET links=? WHERE id=?", (links, task_id)
                    )
                elif not (self.app.store.one("SELECT links FROM tasks WHERE id=?", (task_id,)) or {}).get("links"):
                    return self._err(400, "这个任务没有保存原始链接，重试时请把链接一起发过来")
            getattr(self.app.runner, action)(task_id)
            return self._send(200, {"ok": True})

        return self._err(404, "not found")

    # ---------- 辅助 ----------
    def _provider_caps(self) -> list[dict]:
        out = []
        for name in names():
            try:
                out.append(create(name, "").describe())
            except Exception as e:  # noqa: BLE001
                out.append({"name": name, "label": name, "color": "#666", "short": name[:2],
                            "ready": False, "error": str(e)})
        return out

    def _static(self, rel: str):
        safe = posixpath.normpath("/" + rel).lstrip("/")
        full = os.path.join(STATIC_DIR, safe.replace("/", os.sep))
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)) or not os.path.isfile(full):
            return self._err(404, "not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            self._send(200, None, ctype, raw=f.read())


def serve(host: str, port: int, db_path: str, workers: int = 2) -> ThreadingHTTPServer:
    app = App(db_path, workers=workers)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    return httpd
