# -*- coding: utf-8 -*-
"""任务引擎：状态机 + 线程池 + 重试 + 断点。

状态迁移（单向，除 pause/resume 与 retry 回退）：
    pending -> resolving -> listing -> transferring -> verifying -> done
                    |           |            |
                    +-----------+------------+--> failed（fatal）
    任意状态 --pause--> paused --resume--> pending
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from .db import Store, now
from .providers import create
from .providers.base import AuthExpired, ProviderError, ProviderNotReady

TERMINAL = {"done", "failed", "canceled"}
MAX_ATTEMPTS = 3


class TaskRunner:
    def __init__(self, store: Store, workers: int = 2):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="task")
        self._lock = threading.Lock()
        self._paused: set[str] = set()
        self._canceled: set[str] = set()

    # ---------------- 对外 ----------------
    def create_task(self, provider: str, links: str, target: str, credential: str = "") -> str:
        task_id = uuid.uuid4().hex[:12]
        ts = now()
        self.store.run(
            "INSERT INTO tasks(id,provider,target,status,total,done,failed,error,created_at,updated_at)"
            " VALUES(?,?,?,?,0,0,0,'',?,?)",
            (task_id, provider, target, "pending", ts, ts),
        )
        with self._lock:
            setattr(self, f"_links_{task_id}", links)
            setattr(self, f"_cred_{task_id}", credential)
        self.pool.submit(self._run, task_id)
        return task_id

    def pause(self, task_id: str) -> None:
        with self._lock:
            self._paused.add(task_id)
        self._set_status(task_id, "paused")

    def resume(self, task_id: str) -> None:
        with self._lock:
            self._paused.discard(task_id)
            self._canceled.discard(task_id)
        self._set_status(task_id, "pending", force=True)
        self.pool.submit(self._run, task_id)

    def cancel(self, task_id: str) -> None:
        with self._lock:
            self._canceled.add(task_id)
        self._set_status(task_id, "canceled")

    def view(self, task_id: str) -> dict | None:
        t = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not t:
            return None
        t["items"] = self.store.all(
            "SELECT src,size,digest,dst,status,error FROM items WHERE task_id=? ORDER BY id", (task_id,)
        )
        return t

    # ---------------- 内部 ----------------
    def _set_status(self, task_id: str, status: str, error: str | None = None, force: bool = False) -> None:
        cur = self.store.one("SELECT status FROM tasks WHERE id=?", (task_id,))
        if cur is None:
            return
        # paused / canceled 是"用户意图"，工作线程不得覆盖；只有用户主动 resume 能改
        if not force and cur["status"] in ("paused", "canceled") and status not in ("paused", "canceled"):
            return
        if error is None:
            self.store.run("UPDATE tasks SET status=?,updated_at=? WHERE id=?", (status, now(), task_id))
        else:
            self.store.run(
                "UPDATE tasks SET status=?,error=?,updated_at=? WHERE id=?", (status, error, now(), task_id)
            )

    def _is_paused(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._paused

    def _is_canceled(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._canceled

    def _run(self, task_id: str) -> None:
        task = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not task or task["status"] in TERMINAL:
            return
        links = getattr(self, f"_links_{task_id}", "")
        cred = getattr(self, f"_cred_{task_id}", "")
        try:
            if self._is_canceled(task_id):
                return
            self._set_status(task_id, "resolving")
            prov = create(task["provider"], cred)

            refs = prov.parse_links(links)
            if not refs:
                raise ProviderNotReady("没有解析到分享链接，请检查粘贴内容")

            # listing
            entries: list[tuple] = []
            for ref in refs:
                if self._is_paused(task_id) or self._is_canceled(task_id):
                    return
                self._set_status(task_id, "listing")
                for e in prov.list_share(ref):
                    entries.append((ref, e))
            if not entries:
                raise ProviderNotReady("分享内没有可用文件")

            self.store.run("UPDATE tasks SET total=?,updated_at=? WHERE id=?", (len(entries), now(), task_id))
            self._set_status(task_id, "transferring")

            # 续跑：已完成条目直接跳过，失败条目重试（pause/resume 与进程重启都走这条路径）
            prev = self.store.all("SELECT src,status FROM items WHERE task_id=?", (task_id,))
            done_set = {r["src"] for r in prev if r["status"] == "done"}
            done = len(done_set)
            failed = 0
            for ref, entry in entries:
                if entry.path in done_set:
                    continue
                if self._is_paused(task_id) or self._is_canceled(task_id):
                    return
                ok, dst, err = self._transfer_with_retry(prov, ref, entry, task["target"])
                self.store.run(
                    "INSERT INTO items(task_id,src,size,digest,dst,status,error) VALUES(?,?,?,?,?,?,?)"
                    " ON CONFLICT(task_id,src) DO UPDATE SET status=excluded.status,dst=excluded.dst,error=excluded.error",
                    (task_id, entry.path, entry.size, entry.digest, dst, "done" if ok else "failed", err or ""),
                )
                if ok:
                    done += 1
                else:
                    failed += 1
                self.store.run(
                    "UPDATE tasks SET done=?,failed=?,updated_at=? WHERE id=?", (done, failed, now(), task_id)
                )

            self._set_status(task_id, "verifying")
            self._set_status(task_id, "done")
        except AuthExpired as e:
            self._set_status(task_id, "failed", f"登录态失效：{e}")
        except ProviderNotReady as e:
            self._set_status(task_id, "failed", str(e))
        except Exception as e:  # noqa: BLE001 - 兜底，任务不能卡死
            self._set_status(task_id, "failed", f"{type(e).__name__}: {e}")
            traceback.print_exc()

    def _transfer_with_retry(self, prov, ref, entry, target: str) -> tuple[bool, str, str]:
        last = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                dst = prov.transfer(ref, entry, target)
                return True, dst, ""
            except ProviderError as e:
                last = str(e)
                time.sleep(min(2 ** attempt, 5) * 0.3)
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                break
        return False, "", last

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
