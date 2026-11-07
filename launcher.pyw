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


def child_env() -> dict:
    """给浏览器子进程的环境：只留系统必需项。

    排查结论：由打包后的 exe 启动浏览器时，窗口能开但页面不渲染（标题停在
    `127.0.0.1/`、渲染进程不响应）；同样的参数由 python 启动就正常。
    差异只可能在继承的环境变量里——PyInstaller 会给进程塞 `_PYI_*`、`_MEI*`
    并把 `_internal` 拼到 PATH 前面。所以这里干脆从零搭一份最小环境，
    不继承任何可疑项。
    """
    sysroot = os.environ.get("SystemRoot") or r"C:\Windows"
    keep = {
        "SystemRoot": sysroot,
        "SystemDrive": os.environ.get("SystemDrive") or "C:",
        "windir": sysroot,
        "TEMP": os.environ.get("TEMP") or os.path.join(sysroot, "Temp"),
        "TMP": os.environ.get("TMP") or os.path.join(sysroot, "Temp"),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "APPDATA": os.environ.get("APPDATA", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        "ProgramData": os.environ.get("ProgramData", ""),
        "ProgramFiles": os.environ.get("ProgramFiles", ""),
        "PROGRAMFILES(X86)": os.environ.get("PROGRAMFILES(X86)", ""),
        "HOMEDRIVE": os.environ.get("HOMEDRIVE", ""),
        "HOMEPATH": os.environ.get("HOMEPATH", ""),
        "USERNAME": os.environ.get("USERNAME", ""),
        "USERDOMAIN": os.environ.get("USERDOMAIN", ""),
        "COMPUTERNAME": os.environ.get("COMPUTERNAME", ""),
        "NUMBER_OF_PROCESSORS": os.environ.get("NUMBER_OF_PROCESSORS", "1"),
        "PROCESSOR_ARCHITECTURE": os.environ.get("PROCESSOR_ARCHITECTURE", "AMD64"),
        "OS": "Windows_NT",
        "COMSPEC": os.environ.get("COMSPEC") or os.path.join(sysroot, "system32", "cmd.exe"),
        "PATHEXT": os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD",
        "PATH": os.pathsep.join([
            os.path.join(sysroot, "system32"),
            sysroot,
            os.path.join(sysroot, "System32", "Wbem"),
        ]),
    }
    return {k: v for k, v in keep.items() if v}


def find_browser() -> str:
    forced = os.environ.get("PANTRANSFER_BROWSER_EXE", "").strip()
    if forced and os.path.exists(forced):
        return forced
    for p in BROWSER_CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise RuntimeError("没找到 Edge / Chrome，无法打开应用窗口")


def _browser_args(url: str, mode: str) -> list:
    """拼浏览器参数。mode: "app" 无地址栏应用窗口 / "new-window" 普通新窗口。"""
    exe = find_browser()
    profile = os.path.join(data_dir(), "ui-profile")
    os.makedirs(profile, exist_ok=True)
    head = [exe]
    if mode == "app":
        head.append(f"--app={url}")
    else:
        head += ["--new-window", url]
    return head + [
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


def _launch_via_start(args: list) -> None:
    """用 `cmd /c start` 拉起浏览器。

    不直接 Popen：由打包后的 exe 直接当浏览器父进程时，窗口能开但页面不渲染。
    经过 cmd / start 这一层，浏览器不再是我们的直接子进程；同时继承环境
    （传精简环境会让 explorer.exe 报 0xc0000142，浏览器也可能起不来）。
    """
    cmd = ["cmd.exe", "/c", "start", ""] + args
    log("launch: " + " ".join(cmd[4:]))
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )


def try_app_window(url: str, timeout: float = 18.0) -> bool:
    """开无地址栏应用窗口，并确认页面真的渲染出来（窗口标题变成页面标题）。"""
    try:
        _launch_via_start(_browser_args(url, "app"))
    except Exception as exc:
        log(f"app window launch failed: {exc!r}")
        return False
    return wait_page_loaded(timeout)


def try_new_window(url: str, timeout: float = 18.0) -> bool:
    """开普通浏览器新窗口（有地址栏），同样确认页面渲染。"""
    try:
        _launch_via_start(_browser_args(url, "new-window"))
    except Exception as exc:
        log(f"new window launch failed: {exc!r}")
        return False
    return wait_page_loaded(timeout)


def open_in_browser(url: str) -> str:
    """打开界面，返回实际成功的通道名。

    三次尝试，每次都靠窗口标题确认"页面真的渲染出来了"，不是开个窗口就算成功：
      1. 应用窗口（无地址栏）—— 最像桌面软件；
      2. 普通浏览器新窗口（有地址栏）；
      3. `os.startfile`，交给系统默认浏览器（可能开在已有窗口的新标签页）。

    实测教训（别改回去）：
    - 由打包后的 exe **直接 Popen 浏览器**时，窗口能开但页面不渲染（标题停在
      `127.0.0.1/`、渲染进程不响应）；
    - 给 `explorer.exe` 传**精简过的环境**会让它报 0xc0000142（Application Error），
      所以所有拉起动作都继承环境，只有我们自己用的 PATH 才做处理。
    """
    if os.environ.get("PANTRANSFER_NO_APPWINDOW") != "1":
        if try_app_window(url):
            return "app window"
        log("app window did not render, trying a normal browser window")
        if try_new_window(url):
            return "browser window"

    try:
        os.startfile(url)
        return "startfile"
    except OSError as exc:
        log(f"startfile failed: {exc!r}")

    subprocess.Popen(["explorer.exe", url])   # 继承环境，绝不传 child_env()
    return "explorer"


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
    # --serve：只起服务，不碰浏览器。给 start_app.vbs 用——
    # 由 exe 自己拉起浏览器时页面渲染不出来（实测），交给 wscript/explorer 一级就正常。
    serve_only = "--serve" in sys.argv[1:]
    try:
        # 1. 已经有实例在跑？直接开窗口，不重复起服务
        for port in (remembered_port(), DEFAULT_PORT):
            if not port:
                continue
            if health(port):
                log(f"reuse running instance on {port}")
                if serve_only:
                    return 0
                open_in_browser(f"http://127.0.0.1:{port}/")
                wait_idle(port)
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

        # 3. 打开界面。--serve 模式不打开，交给外部（start_app.vbs）开。
        if serve_only:
            log("serve-only mode, not opening a window")
            wait_idle(port)
            log("idle, stopping server")
            _stop(httpd)
            return 0

        try:
            how = open_in_browser(url)
            log(f"opened via {how}: {url}")
        except Exception as exc:
            log("open interface failed: " + repr(exc))
            message_box(f"打不开界面：{exc}\n\n请手动访问 {url}")
            return 1

        if not wait_page_loaded(timeout=40.0):
            # 窗口标题没变成页面标题：可能浏览器还没加载完、也可能被弹窗挡住。
            # 不退出——服务继续跑，用户自己刷新页面即可。
            log("page did not report itself loaded, keep server running")
        wait_idle(port)
        log("window closed, stopping server")
        _stop(httpd)
        return 0
    except Exception as exc:
        log("fatal: " + traceback.format_exc())
        message_box(f"启动失败：{exc}\n\n日志：{os.path.join(log_dir(), 'launcher.log')}")
        return 1


def _stop(httpd) -> None:
    try:
        httpd.shutdown()
        httpd.server_close()
        httpd.app.close()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
