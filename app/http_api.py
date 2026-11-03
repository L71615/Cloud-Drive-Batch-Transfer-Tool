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
from .db import Store, now
from .providers import create, names
from .providers.base import AuthExpired, ProviderError, ProviderNotReady
from .tasks import TaskRunner

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TASK_ACTION = re.compile(r"^/api/tasks/([0-9a-zA-Z]+)/(pause|resume|cancel|retry)$")
TASK_ONE = re.compile(r"^/api/tasks/([0-9a-zA-Z]+)$")
ACCOUNT_ONE = re.compile(r"^/api/accounts/(\d+)$")
ACCOUNT_CHECK = re.compile(r"^/api/accounts/(\d+)/check$")


class App:
    def __init__(self, db_path: str, workers: int = 2):
        self.store = Store(db_path)
        self.runner = TaskRunner(self.store, workers=workers)

    def close(self) -> None:
        self.runner.shutdown()
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
        if path == "/api/accounts":
            rows = self.app.store.all(
                "SELECT id,provider,name,created_at FROM accounts ORDER BY id DESC"
            )
            return self._send(200, {"ok": True, "accounts": rows})
        if path == "/api/tasks":
            limit = min(int(parse_qs(u.query).get("limit", ["100"])[0]), 500)
            rows = self.app.store.all(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
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

        if path == "/api/accounts":
            provider = str(body.get("provider") or "")
            cred = str(body.get("credential") or "").strip()
            if provider not in names():
                return self._err(400, f"unknown provider: {provider}")
            if not cred:
                return self._err(400, "credential 不能为空")
            self.app.store.run(
                "INSERT INTO accounts(provider,name,credential,created_at) VALUES(?,?,?,?)",
                (provider, str(body.get("name") or provider), cred, now()),
            )
            row = self.app.store.one("SELECT id FROM accounts ORDER BY id DESC LIMIT 1") or {}
            return self._send(200, {"ok": True, "id": row.get("id")})

        if path == "/api/parse":
            provider = str(body.get("provider") or "")
            try:
                prov = create(provider, "")
            except KeyError:
                return self._err(400, f"unknown provider: {provider}")
            refs = prov.parse_links(str(body.get("text") or ""))
            return self._send(200, {
                "ok": True,
                "count": len(refs),
                "refs": [r.__dict__ for r in refs],
            })

        if path == "/api/tasks":
            provider = str(body.get("provider") or "")
            links = str(body.get("links") or "").strip()
            target = str(body.get("target") or "/PanTransfer").strip() or "/PanTransfer"
            if provider not in names():
                return self._err(400, f"unknown provider: {provider}")
            if not links:
                return self._err(400, "链接不能为空")
            cred = str(body.get("credential") or "").strip()
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
