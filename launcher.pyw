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
import webbrowser

APP_NAME = "PanTransfer"
# 页面 <title>：窗口加载完成后窗口标题就是这个，用来判断窗口是否已经开起来
APP_TITLE = "PanTransfer · 批量转存"
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
    if os.environ.get("PANTRANSFER_UI_TAB") == "1":
        # 调试用：不开应用窗口，改成普通标签页，用来判断白屏是不是 --app 模式导致
        args[1] = url
    extra = os.environ.get("PANTRANSFER_UI_ARGS", "").strip()
    if extra:
        args += extra.split()
    log("open window: " + " ".join(args))
    # 打包后进程里带着 PyInstaller 自己的环境变量（_PYI_* / _MEI*），
    # 传给浏览器会让它的渲染进程起不来（窗口白屏），这里清掉。
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("_PYI", "_MEI", "PYINSTALLER"))}
    if os.environ.get("PANTRANSFER_SHELL") == "1":
        # 交给 Windows Shell 启动：浏览器进程不再是我们 exe 的子进程，
        # 用于排查"打包后子进程渲染异常"这类环境问题。
        rc = ctypes.windll.shell32.ShellExecuteW(None, "open", args[0], " ".join(args[1:]), None, 1)
        log(f"ShellExecuteW -> {rc}")
        return None
    # CREATE_NO_WINDOW：不要给浏览器弹出控制台
    # 三路输出接到空设备：打包成 GUI 程序后自身没有标准句柄，
    # 直接继承会让浏览器子进程拿到无效句柄（表现是窗口白屏、渲染进程不响应）。
    return subprocess.Popen(
        args,
        creationflags=0x08000000 if os.name == "nt" else 0,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_idle(port: int, limit: float = 1800.0) -> None:
    """退回浏览器模式时用：半天没人访问就把后台服务关掉，别留隐形进程。"""
    while True:
        info = health(port)
        if info is None:
            return
        if float(info.get("idle") or 0) > limit:
            log(f"idle {limit:.0f}s, stopping server")
            return
        time.sleep(20.0)


def wait_page_loaded(timeout: float = 25.0) -> bool:
    """等窗口标题变成页面标题，说明界面真的渲染出来了。"""
    start = time.time()
    while time.time() - start < timeout:
        if _window_exists(APP_TITLE):
            return True
        time.sleep(0.8)
    return False


def wait_window_gone(port: int, boot_timeout: float = 120.0) -> None:
    """Shell 启动时拿不到进程句柄，就靠窗口标题判断：出现过后消失即视为关闭。"""
    want = f"127.0.0.1:{port}"
    seen = False
    start = time.time()
    while True:
        alive = _window_exists(want) or _window_exists(APP_TITLE)
        if alive:
            seen = True
        elif seen:
            return
        elif time.time() - start > boot_timeout:
            # 窗口一直没出现：不再无限等，交给用户关进程
            log("window never appeared, keep server running")
            return
        time.sleep(1.5)


def _window_exists(needle: str) -> bool:
    try:
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _lparam):
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if needle and needle in buf.value:
                found.append(buf.value)
                return False
            return True

        user32.EnumWindows(_cb, None)
        return bool(found)
    except Exception:
        return False


def message_box(text: str, title: str = APP_NAME) -> None:
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
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return 1

        if proc is None:
            # Shell 启动拿不到进程句柄：改成轮询窗口是否还在
            if not wait_page_loaded():
                log("window did not show the page, opening default browser")
                webbrowser.open(url)
                wait_idle(port)
            else:
                wait_window_gone(port)
        else:
            if not wait_page_loaded():
                # 应用窗口起不来（个别环境的浏览器问题）：关掉白窗口，退回默认浏览器，
                # 界面一样能用，只是多了地址栏。
                log("window did not show the page, opening default browser")
                try:
                    proc.terminate()
                except Exception:
                    pass
                webbrowser.open(url)
                wait_idle(port)
            else:
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
