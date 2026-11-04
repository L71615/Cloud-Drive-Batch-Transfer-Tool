# -*- coding: utf-8 -*-
"""启动入口：python run.py [--port 8765] [--open]"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import threading
import time
import webbrowser


def backup_db(db_path: str, keep: int = 3) -> None:
    """启动时给库留一份快照：强杀进程时 WAL 可能没落盘，任务明细就没了。"""
    if not os.path.exists(db_path):
        return
    folder = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backup")
    try:
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, time.strftime("pantransfer-%Y%m%d-%H%M%S.db"))
        src = sqlite3.connect(db_path)
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
        src.close()
        olds = sorted(f for f in os.listdir(folder) if f.endswith(".db"))[:-keep]
        for name in olds:
            os.remove(os.path.join(folder, name))
    except Exception as exc:  # noqa: BLE001 - 备份失败不影响启动
        print(f"[warn] 数据库备份跳过：{exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pantransfer", description="本地批量转存工具")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=os.path.join(os.getcwd(), "data", "pantransfer.db"))
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    ap.add_argument("--gateway", default=os.environ.get("PANTRANSFER_GATEWAY", ""),
                    help="离线联调：把三个网盘指向本地假网盘，例如 http://127.0.0.1:8899")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.providers import apply_dev_gateway  # 延迟导入，保证 --help 不依赖包路径
    from app.http_api import serve

    backup_db(args.db)

    if args.gateway:
        apply_dev_gateway(args.gateway)
        print(f"[dev] 使用本地假网盘网关: {args.gateway}")

    httpd = serve(args.host, args.port, args.db, workers=args.workers)
    url = f"http://{args.host}:{args.port}/"
    print(f"PanTransfer ready: {url}")
    print(f"db: {os.path.abspath(args.db)}")
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.app.close()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
