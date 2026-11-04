"""浏览器代登录（扫码 / 账号密码都由官方页面自己完成）。

思路：不实现任何网盘的登录协议，也不带任何 AppKey。用系统自带的 Edge 开一个
**独立 profile** 的窗口，直接打开官方登录页；用户在官方页面里扫码，登录成功后
我们通过 Chrome DevTools Protocol（`--remote-debugging-port`）只读取该域名下的
Cookie，交给 `app.credentials` 校验后落库。

这样做的好处：
  * 二维码、验证码、二次验证全部由官方页面负责，接口改版不影响我们；
  * 不需要开放平台资质，也不需要反编译任何客户端的密钥；
  * 三个网盘走同一套代码，只是登录页 URL 与 Cookie 域名不同。

仅依赖标准库：socket + base64 + struct 手写最小 WebSocket 客户端。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

from .credentials import validate

LOGIN_URL = {
    "baidu": "https://pan.baidu.com/",
    "quark": "https://pan.quark.cn/",
    "uc": "https://drive.uc.cn/",
}

COOKIE_DOMAIN = {
    "baidu": "baidu.com",
    "quark": "quark.cn",
    "uc": "uc.cn",
}

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


class BrowserLoginError(RuntimeError):
    pass


def find_browser() -> str:
    for path in EDGE_CANDIDATES:
        if os.path.isfile(path):
            return path
    raise BrowserLoginError("没找到 Edge / Chrome，请先安装任意一个现代浏览器")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------- WebSocket 最小实现


def _ws_connect(ws_url: str, timeout: float = 10.0) -> socket.socket:
    u = urlparse(ws_url)
    port = u.port or 80
    sock = socket.create_connection((u.hostname or "127.0.0.1", port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    path = u.path or "/"
    if u.query:
        path += "?" + u.query
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {u.hostname}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise BrowserLoginError("调试端口拒绝连接（浏览器未就绪）")
        buf += chunk
    head = buf.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    if " 101 " not in head.split("\r\n")[0]:
        raise BrowserLoginError(f"WebSocket 握手失败：{head.splitlines()[0]}")
    return sock


def _ws_send(sock: socket.socket, text: str) -> None:
    data = text.encode()
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    mask = os.urandom(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + masked)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise BrowserLoginError("调试连接已关闭")
        out += chunk
    return out


def _ws_recv(sock: socket.socket) -> str:
    while True:
        b1, b2 = _recv_exact(sock, 2)
        opcode = b1 & 0x0F
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
        payload = _recv_exact(sock, length) if length else b""
        if opcode == 0x9:  # ping
            sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
            continue
        if opcode == 0x8:
            raise BrowserLoginError("调试连接被浏览器关闭")
        if opcode in (0x1, 0x0):
            return payload.decode("utf-8", "replace")


class _CDP:
    def __init__(self, ws_url: str):
        self.sock = _ws_connect(ws_url)
        self.next_id = 0

    def call(self, method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
        self.next_id += 1
        mid = self.next_id
        _ws_send(self.sock, json.dumps({"id": mid, "method": method, "params": params or {}}))
        self.sock.settimeout(timeout)
        while True:
            msg = json.loads(_ws_recv(self.sock))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise BrowserLoginError(str(msg["error"]))
                return msg.get("result") or {}
            # 忽略事件推送

    def close(self) -> None:
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def _http_json(port: int, path: str) -> list | dict:
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _page_target(port: int, timeout: float = 12.0) -> tuple[str, str]:
    """等浏览器把调试接口与页面 target 都准备好。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            targets = _http_json(port, "/json/list")
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"], t.get("url", "")
        except (urllib.error.URLError, OSError, ValueError) as e:  # noqa: PERF203
            last = e
        time.sleep(0.3)
    raise BrowserLoginError(f"浏览器调试端口未就绪：{last}")


def read_cookies(port: int, domain: str) -> str:
    ws_url, _ = _page_target(port)
    cdp = _CDP(ws_url)
    try:
        cdp.call("Network.enable")
        res = cdp.call("Network.getAllCookies")
    finally:
        cdp.close()
    jars = [c for c in res.get("cookies", []) if domain in (c.get("domain") or "")]
    # 同名 Cookie 可能存在于多个子域，保留最长的那份
    best: dict[str, dict] = {}
    for c in jars:
        name = c.get("name")
        if not name:
            continue
        if name not in best or len(c.get("domain", "")) > len(best[name].get("domain", "")):
            best[name] = c
    return "; ".join(f"{k}={v.get('value', '')}" for k, v in best.items())


# ---------------------------------------------------------------- 会话管理


class Session:
    def __init__(self, provider: str):
        self.id = uuid.uuid4().hex[:12]
        self.provider = provider
        self.port = _free_port()
        self.profile = os.path.join(tempfile.gettempdir(), f"pantransfer-login-{self.id}")
        self.proc: subprocess.Popen | None = None
        self.status = "starting"      # starting | waiting | ready | error | closed
        self.message = "正在打开登录窗口…"
        self.cookie = ""
        self.count = 0
        self.created = time.time()
        self._lock = threading.Lock()

    def launch(self) -> None:
        exe = find_browser()
        os.makedirs(self.profile, exist_ok=True)
        args = [
            exe,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=msEdgeWelcomePage",
            LOGIN_URL[self.provider],
        ]
        creation = 0x08000000 if os.name == "nt" else 0
        if os.environ.get("PANTRANSFER_LOGIN_HEADLESS"):
            args.insert(1, "--headless=new")
        self.proc = subprocess.Popen(args, creationflags=creation,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.status = "waiting"
        self.message = "请在弹出的浏览器窗口里登录（支持扫码），登录后这里会自动完成"

    def poll(self) -> dict:
        if self.status in ("error", "closed"):
            return self.view()
        try:
            raw = read_cookies(self.port, COOKIE_DOMAIN[self.provider])
        except BrowserLoginError as e:
            self.message = str(e)
            return self.view()
        if not raw:
            self.message = "还没检测到登录态，请在浏览器窗口里完成登录"
            return self.view()
        v = validate(self.provider, raw)
        if not v["ok"]:
            self.message = f"已捕获 Cookie，但{v['error']}"
            return self.view()
        self.cookie = v["cookie"]
        self.count = v["count"]
        self.status = "ready"
        self.message = f"登录成功，识别到 {v['count']} 个字段"
        return self.view()

    def close(self) -> None:
        self.status = "closed"
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        shutil.rmtree(self.profile, ignore_errors=True)

    def view(self) -> dict:
        return {
            "sid": self.id,
            "provider": self.provider,
            "status": self.status,
            "message": self.message,
            "cookie": self.cookie if self.status == "ready" else "",
            "count": self.count,
            "port": self.port,
        }


class Manager:
    """进程内会话表；同一网盘只保留一个窗口，重复点击复用。"""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def start(self, provider: str) -> dict:
        if provider not in LOGIN_URL:
            raise BrowserLoginError(f"该网盘没有登录页：{provider}")
        with self._lock:
            for s in list(self._sessions.values()):
                if s.provider == provider and s.status in ("starting", "waiting", "ready"):
                    return s.view()
            self._gc()
            s = Session(provider)
            self._sessions[s.id] = s
        s.launch()
        return s.view()

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def close(self, sid: str) -> None:
        s = self._sessions.pop(sid, None)
        if s:
            s.close()

    def close_all(self) -> None:
        for sid in list(self._sessions):
            self.close(sid)

    def _gc(self) -> None:
        now = time.time()
        for sid, s in list(self._sessions.items()):
            if s.status in ("closed", "error") or now - s.created > 1800:
                self.close(sid)


MANAGER = Manager()
