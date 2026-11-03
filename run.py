# -*- coding: utf-8 -*-
"""启动入口：python run.py [--port 8765] [--open]"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pantransfer", description="本地批量转存工具")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=os.path.join(os.getcwd(), "data", "pantransfer.db"))
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.http_api import serve  # 延迟导入，保证 --help 不依赖包路径

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
