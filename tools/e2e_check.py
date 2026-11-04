# -*- coding: utf-8 -*-
"""端到端自检：假网盘 + 真 HTTP 服务 + 真任务引擎，一条命令验证全链路。

    python tools/e2e_check.py

它会临时起两个本地服务（随机端口），跑完自动关闭，不碰真实网盘、
不污染 data/ 目录。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.http_api import serve                    # noqa: E402
from app.providers import apply_dev_gateway       # noqa: E402
from tools.mock_netdisk import serve as mock_serve  # noqa: E402


def call(base: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def wait(base: str, task_id: str, timeout: float = 30.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        v = call(base, f"/api/tasks/{task_id}")["task"]
        if v["status"] in ("done", "failed", "canceled"):
            return v
        time.sleep(0.2)
    raise SystemExit(f"任务 {task_id} 超时未结束")


def main() -> int:
    mock = mock_serve(0)
    gateway = f"http://127.0.0.1:{mock.server_address[1]}"
    apply_dev_gateway(gateway)

    db = os.path.join(tempfile.mkdtemp(prefix="pt-e2e-"), "e2e.db")
    httpd = serve("127.0.0.1", 0, db, workers=4)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"mock={gateway}  app={base}")

    meta = call(base, "/api/meta")
    print("providers:", [(p["name"], p["label"], p["ready"]) for p in meta["providers"]])

    acc = call(base, "/api/accounts", "POST",
               {"provider": "baidu", "name": "自检账号", "credential": "BDUSS=selftest; STOKEN=x"})
    print("account:", acc.get("id"))

    new = call(base, "/api/tasks", "POST", {
        "provider": "baidu",
        "links": "https://pan.baidu.com/s/1AbCdEf 提取码: x9y8",
        "target": "/PanTransfer",
        "account_id": acc["id"],
    })
    v = wait(base, new["id"])
    print(f"baidu: {v['status']} total={v['total']} done={v['done']} failed={v['failed']}")
    for it in v["items"]:
        print("   ", it["status"], it["src"], "->", it["dst"])
    assert v["status"] == "done", v.get("error")
    assert v["total"] == 3 and v["failed"] == 0

    q = call(base, "/api/tasks", "POST", {
        "provider": "quark",
        "links": "https://pan.quark.cn/s/a1b2c3d4e5f6",
        "target": "/PanTransfer/quark",
        "credential": "__pus=selftest",
    })
    qv = wait(base, q["id"])
    print(f"quark: {qv['status']} total={qv['total']} done={qv['done']}")
    assert qv["status"] == "done" and qv["total"] == 2, qv.get("error")

    st = call(base, "/api/stats")
    print("stats:", {k: st[k] for k in ("running", "done", "failed", "files")})

    back = call(base, "/api/tasks/" + new["id"], "GET")["task"]
    ok = sum(1 for i in back["items"] if i["status"] == "done")
    print(f"detail items done={ok}")

    # 目录浏览：转存完成后，用户盘里应能看到 /PanTransfer
    br = call(base, "/api/browse?provider=baidu&path=%2FPanTransfer&account_id=" + str(acc["id"]))
    names = [x["name"] for x in br["items"]]
    print("browse /PanTransfer:", names)
    assert "a.txt" in names, names

    # 协议诊断：四步全过，且能拿到原始响应
    dg = call(base, "/api/diag", "POST", {
        "provider": "baidu",
        "link": "https://pan.baidu.com/s/1AbCdEf 提取码: x9y8",
        "account_id": acc["id"],
    })
    print("diag:", [(s["name"], s["ok"]) for s in dg["steps"]])
    assert all(s["ok"] for s in dg["steps"]), dg["steps"]
    assert dg["raw"], "诊断应记录原始 HTTP 响应"
    print("raw 请求数:", len(dg["raw"]))
    print("\n✅ 全链路自检通过")
    httpd.shutdown()
    httpd.app.close()
    httpd.server_close()
    mock.shutdown()
    mock.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
