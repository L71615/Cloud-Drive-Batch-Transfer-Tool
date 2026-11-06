# -*- coding: utf-8 -*-
"""桌面启动器：双击就出现一个独立窗口（不带地址栏、不是浏览器标签页）。

做三件事：
1. 在后台线程里起本地服务（复用 app.http_api.serve），只监听 127.0.0.1；
2. 用系统 Edge / Chrome 的 --app 模式打开一个"应用窗口"，窗口自带图标、可挂任务栏；
3. 窗口关掉 → 服务跟着退出，不留后台进程。

如果已经有实例在跑（端口上能读到 /api/health），就不再起第二个，直接开窗口。
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

APP_NAME = "PanTransfer"
DEFAULT_PORT = 8765


# ---------------------------------------------------------------- 路径

def app_dir() -> str:
    """程序所在目录：打包后是 exe 所在目录，源码运行时是仓库根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """数据库与日志都放在程序旁边（便携式，整个文件夹可以随便挪）。"""
    d = os.path.join(app_dir(), "data")
    os.makedirs(d, exist_ok=True)
    return d


def log_dir() -> str:
    try:
        return data_dir()
    except Exception:
        import tempfile
        return tempfile.gettempdir()


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(os.path.join(log_dir(), "launcher.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 服务

def health(port: int, timeout: float = 0.8):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            # 认准是我们自己：别的程序占着同一个端口时会返回别的东西
            return data if "version" in data and "providers" in data else None
    except Exception:
        return None


def port_free(port: int) -> bool:
    """真能绑上才算空：Windows 上 SO_REUSEADDR 会让"看似能绑"其实是抢别人的端口。"""
    s = socket.socket()
    try:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", 0x8000000), 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def candidate_ports():
    seen = set()
    order = [DEFAULT_PORT] + list(range(DEFAULT_PORT + 1, DEFAULT_PORT + 20))
    import random
    order += [random.randint(20000, 30000) for _ in range(8)]
    for p in order:
        if p in seen:
            continue
        seen.add(p)
        yield p


class PortBusy(RuntimeError):
    pass


def port_file() -> str:
    return os.path.join(data_dir(), "current_port")


def remembered_port():
    """上次开的端口写在这里，第二次双击时直接复用，不会又起一个服务。"""
    try:
        with open(port_file(), "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def start_server(port: int):
    """进程内起服务并确认它真的在应答，返回 httpd。端口被抢时抛 PortBusy。"""
    if getattr(sys, "frozen", False):
        sys.path.insert(0, getattr(sys, "_MEIPASS", ""))
    from app.http_api import serve
    import run as run_mod

    db = os.path.join(data_dir(), "pantransfer.db")
    try:
        run_mod.backup_db(db)
    except Exception:
        pass

    httpd = serve("127.0.0.1", port, db, workers=int(os.environ.get("PANTRANSFER_WORKERS", "2")))
    t = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    t.start()

    deadline = time.time() + 6
    while time.time() < deadline:
        if health(port, timeout=0.3):
            try:
                with open(port_file(), "w", encoding="utf-8") as f:
                    f.write(str(port))
            except Exception:
                pass
            return httpd
        if not t.is_alive():
            break
        time.sleep(0.12)
    try:
        httpd.shutdown()
        httpd.server_close()
        httpd.app.close()
    except Exception:
        pass
    raise PortBusy(f"端口 {port} 被占用或服务没起来")


# ---------------------------------------------------------------- 窗口

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Edge\Application\msedge.exe"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]


def find_browser() -> str:
    for p in BROWSER_CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise RuntimeError("没找到 Edge / Chrome，无法打开应用窗口")


def open_window(url: str) -> subprocess.Popen:
    exe = find_browser()
    # 独立的浏览器 profile：窗口自带图标、不跟日常浏览器混在一起
    profile = os.path.join(data_dir(), "ui-profile")
    os.makedirs(profile, exist_ok=True)
    args = [
        exe,
        f"--app={url}",
        "--window-size=1280,880",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-extensions",
        "--disable-component-update",
        "--disable-features=msEdgeWelcomePage,msImplicitSignin,EdgeSyncConsent,msEdgeSyncConsent",
        "--disable-background-networking",
    ]
    extra = os.environ.get("PANTRANSFER_UI_ARGS", "").strip()
    if extra:
        args += extra.split()
    log("open window: " + " ".join(args))
    # CREATE_NO_WINDOW：不要给浏览器弹出控制台
    return subprocess.Popen(args, creationflags=0x08000000 if os.name == "nt" else 0)


def message_box(text: str, title: str = APP_NAME) -> None:
    log("message_box: " + text.replace("\n", " / "))
    if os.environ.get("PANTRANSFER_SILENT") == "1":
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


# ---------------------------------------------------------------- 主流程

def main() -> int:
    try:
        # 1. 已经有实例在跑？直接开窗口，不重复起服务
        for port in (remembered_port(), DEFAULT_PORT):
            if not port:
                continue
            if health(port):
                log(f"reuse running instance on {port}")
                open_window(f"http://127.0.0.1:{port}/").wait()
                return 0

        # 2. 起服务：逐个端口试，谁先应答就用谁
        httpd = None
        port = 0
        for cand in candidate_ports():
            if not port_free(cand):
                continue
            try:
                httpd = start_server(cand)
                port = cand
                break
            except PortBusy as exc:
                log(str(exc))
                continue
        if httpd is None:
            raise RuntimeError("没有可用端口（都被别的程序占了）")

        log(f"server ready on {port}")
        url = f"http://127.0.0.1:{port}/"

        # 3. 开应用窗口，窗口关掉就退出
        try:
            proc = open_window(url)
        except Exception as exc:
            message_box(f"打不开应用窗口：{exc}\n\n已用默认浏览器打开。")
            import webbrowser
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return 1

        proc.wait()
        log("window closed, stopping server")
        try:
            httpd.shutdown()
            httpd.server_close()
            httpd.app.close()
        except Exception:
            pass
        return 0
    except Exception as exc:
        log("fatal: " + traceback.format_exc())
        message_box(f"启动失败：{exc}\n\n日志：{os.path.join(log_dir(), 'launcher.log')}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
