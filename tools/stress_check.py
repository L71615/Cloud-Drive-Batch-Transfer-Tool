# -*- coding: utf-8 -*-
"""规模压测：一次任务塞 N 条分享、每条 M 个文件，跑完整流水线。

它问的是"引擎能不能扛住 200 条链接 / 上千文件"，与真实网盘无关：
假网盘按 spec 复刻了分页、token、pdir 校验与落盘，所以耗时可作参考。

    python tools/stress_check.py --shares 200 --files 5 --workers 8
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import mock_netdisk  # noqa: E402
from tools.mock_netdisk import serve  # noqa: E402

from app.db import Store  # noqa: E402
from app.http_api import serve as app_serve  # noqa: E402
from app.providers import apply_dev_gateway  # noqa: E402
from app.tasks import TaskRunner  # noqa: E402

import urllib.request  # noqa: E402


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shares", type=int, default=200)
    ap.add_argument("--files", type=int, default=5, help="每条分享的文件数（含根目录 1 个）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--qps", type=float, default=2.5)
    ap.add_argument("--target", default="/Stress")
    args = ap.parse_args()

    ids = mock_netdisk.install_stress(args.shares, args.files)
    mock = serve(0)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{mock.server_address[1]}"
    apply_dev_gateway(base)

    tmp = Path(tempfile.mkdtemp(prefix="pt-stress-"))
    store = Store(str(tmp / "stress.db"))
    runner = TaskRunner(store, workers=args.workers)
    runner.update_settings({"qps": args.qps})

    cred = "__pus=stress; __puus=stress"
    links = "\n".join(f"https://drive.uc.cn/s/{i}" for i in ids)
    t0 = time.time()
    tid = runner.create_task("uc", links, args.target, cred)
    print(f"task={tid} shares={len(ids)} files/share={args.files} "
          f"workers={args.workers} qps={args.qps}", flush=True)

    last = -1
    while True:
        v = runner.view(tid)
        if v["status"] in ("done", "failed", "canceled"):
            break
        if v["done"] != last:
            last = v["done"]
            print(f"  {v['status']:12s} total={v['total']:5d} done={v['done']:5d} "
                  f"failed={v['failed']:4d}  {time.time() - t0:6.1f}s", flush=True)
        time.sleep(0.5)

    elapsed = time.time() - t0
    items = store.all("SELECT status, COUNT(*) c FROM items WHERE task_id=? GROUP BY status", (tid,))
    counts = {r["status"]: r["c"] for r in items}
    errs = store.all(
        "SELECT error, COUNT(*) c FROM items WHERE task_id=? AND status='failed' "
        "GROUP BY error ORDER BY c DESC LIMIT 5", (tid,))
    print(f"\nfinished: status={v['status']} total={v['total']} done={v['done']} "
          f"failed={v['failed']} in {elapsed:.1f}s")
    print(f"item status: {counts}")
    if errs:
        print("top errors:")
        for r in errs:
            print(f"  {r['c']:4d}  {r['error'][:120]}")

    landed = runner.view(tid)
    print(f"rate: {v['done'] / elapsed:.1f} files/s" if elapsed else "")

    runner.shutdown()
    store.close()
    mock.shutdown()
    mock.server_close()
    return 0 if v["failed"] == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
