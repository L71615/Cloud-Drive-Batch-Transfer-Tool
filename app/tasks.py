# -*- coding: utf-8 -*-
"""任务引擎：状态机 + 线程池 + 重试 + 断点。

状态迁移（单向，除 pause/resume 与 retry 回退）：
    pending -> resolving -> listing -> transferring -> verifying -> done
                    |           |            |
                    +-----------+------------+--> failed（fatal）
    任意状态 --pause--> paused --resume--> pending
"""
from __future__ import annotations

import json
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
SHARE_MAX = 1800   # 秒；单条分享的处理上限（读目录 + 转存），到时才算真的卡住
BATCH_SIZE = 20    # 每批转存的文件数（一次 save 提交多个 fid）

DEFAULT_SETTINGS = {
    "concurrency": 2,
    "qps": 0.8,
    "retries": 3,
    "default_target": "/PanTransfer",
}


class Gate:
    """可动态改变容量的并发闸门（设置里的"同时处理任务数"）。

    线程池固定 8 个 worker，真正限制并发的是这道闸：设置改小立即生效，
    改大则等下一批任务自然补上。
    """

    def __init__(self, n: int = 2):
        self._n = max(1, int(n))
        self._cur = 0
        self._cv = threading.Condition()

    def set(self, n: int) -> None:
        with self._cv:
            self._n = max(1, int(n))
            self._cv.notify_all()

    def __enter__(self):
        with self._cv:
            while self._cur >= self._n:
                self._cv.wait(timeout=0.5)
            self._cur += 1
        return self

    def __exit__(self, *exc):
        with self._cv:
            self._cur -= 1
            self._cv.notify_all()
        return False


class TaskRunner:
    def __init__(self, store: Store, workers: int = 8):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=max(4, workers), thread_name_prefix="task")
        self._lock = threading.Lock()
        self._paused: set[str] = set()
        self._canceled: set[str] = set()
        s = self.settings()
        self._gate = Gate(s["concurrency"])
        self._gate.set(s["concurrency"])

    # ---------------- 设置 ----------------
    def settings(self) -> dict:
        row = self.store.one("SELECT v FROM settings WHERE k='engine'")
        data = dict(DEFAULT_SETTINGS)
        if row:
            try:
                data.update(json.loads(row["v"]))
            except json.JSONDecodeError:
                pass
        return data

    def update_settings(self, patch: dict) -> dict:
        cur = self.settings()
        for k in DEFAULT_SETTINGS:
            if k in patch and patch[k] is not None:
                try:
                    if k == "default_target":
                        cur[k] = str(patch[k]).strip() or DEFAULT_SETTINGS[k]
                    else:
                        cur[k] = max(1, int(float(patch[k]))) if k != "qps" else max(0.2, min(3.0, float(patch[k])))
                except (TypeError, ValueError):
                    continue
        self.store.run(
            "INSERT INTO settings(k,v) VALUES('engine',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (json.dumps(cur, ensure_ascii=False),),
        )
        self._gate.set(cur["concurrency"])
        return cur

    # ---------------- 对外 ----------------
    def create_task(self, provider: str, links: str, target: str, credential: str = "") -> str:
        task_id = uuid.uuid4().hex[:12]
        ts = now()
        self.store.run(
            "INSERT INTO tasks(id,provider,target,status,total,done,failed,error,links,credential,created_at,updated_at)"
            " VALUES(?,?,?,?,0,0,0,'',?,?,?,?)",
            (task_id, provider, target, "pending", links, credential, ts, ts),
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
        t = self.store.one(
            "SELECT id,provider,target,status,total,done,failed,error,links,"
            "created_at,updated_at FROM tasks WHERE id=?", (task_id,),
        )
        if not t:
            return None
        # 登录态绝不外泄给前端；只告诉界面"这个任务有没有存链接"，决定重试时要不要让用户补
        t["has_links"] = bool(t.pop("links", ""))
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

    def retry(self, task_id: str) -> None:
        """失败任务重新排队：保留已完成条目，只重跑未完成的。"""
        self.store.run(
            "UPDATE tasks SET status='pending',error='',done=0,failed=0,updated_at=? WHERE id=?",
            (now(), task_id),
        )
        with self._lock:
            self._canceled.discard(task_id)
            self._paused.discard(task_id)
        self.pool.submit(self._run, task_id)

    def delete(self, task_id: str) -> None:
        self.store.run("DELETE FROM items WHERE task_id=?", (task_id,))
        self.store.run("DELETE FROM tasks WHERE id=?", (task_id,))
        with self._lock:
            self._paused.discard(task_id)
            self._canceled.discard(task_id)

    def recover_orphans(self) -> int:
        """进程重启后，把上次没跑完的任务标成失败并可重试（已完成条目会被跳过）。"""
        rows = self.store.all(
            "SELECT id FROM tasks WHERE status IN"
            " ('pending','resolving','listing','transferring','verifying')"
        )
        for r in rows:
            self.store.run(
                "UPDATE tasks SET status='failed',error=?,updated_at=? WHERE id=?",
                ("程序重启导致任务中断，点「重试」可继续（已完成的文件不会重复转存）", now(), r["id"]),
            )
        return len(rows)

    def stats(self) -> dict:
        row = self.store.one(
            "SELECT"
            " SUM(CASE WHEN status IN ('pending','resolving','listing','transferring','verifying') THEN 1 ELSE 0 END) AS running,"
            " SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,"
            " SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,"
            " SUM(CASE WHEN status='paused' THEN 1 ELSE 0 END) AS paused"
            " FROM tasks"
        ) or {}
        files = self.store.one("SELECT COUNT(*) AS n FROM items WHERE status='done'") or {"n": 0}
        return {
            "running": int(row.get("running") or 0),
            "done": int(row.get("done") or 0),
            "failed": int(row.get("failed") or 0),
            "paused": int(row.get("paused") or 0),
            "files": int(files.get("n") or 0),
        }

    def _run(self, task_id: str) -> None:
        with self._gate:
            self._run_inner(task_id)

    def _run_inner(self, task_id: str) -> None:
        task = self.store.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not task or task["status"] in TERMINAL:
            return
        links = getattr(self, f"_links_{task_id}", "") or task.get("links") or ""
        cred = getattr(self, f"_cred_{task_id}", "") or task.get("credential") or ""
        cfg = self.settings()
        try:
            if self._is_canceled(task_id):
                return
            self._set_status(task_id, "resolving")
            prov = create(task["provider"], cred)
            prov.qps = float(cfg.get("qps") or prov.qps)

            refs = prov.parse_links(links)
            if not refs:
                raise ProviderNotReady("没有解析到分享链接，请检查粘贴内容")

            # 边读目录边转存：大分享不必等整棵树读完才开始，进度条一直在走。
            # 攒够一批再一次 save（官方接口支持 fid_list 数组），十来个文件一次请求，比逐个快很多。
            prev = self.store.all("SELECT src,status FROM items WHERE task_id=?", (task_id,))
            done_set = {r["src"] for r in prev if r["status"] == "done"}
            done = len(done_set)
            failed = 0
            seen = 0
            list_start = time.time()
            buf: list[tuple] = []
            self.store.run(
                "UPDATE tasks SET total=0,done=?,failed=0,updated_at=? WHERE id=?",
                (done, now(), task_id),
            )

            def flush_batch() -> None:
                nonlocal done, failed
                if not buf:
                    return
                ref0 = buf[0][0]
                batch = [e for _, e in buf]
                self._set_status(task_id, "transferring")
                ok, dsts, err = self._batch_with_retry(prov, ref0, batch, task["target"], cfg)
                errs = getattr(prov, "last_batch_errors", []) or []
                for idx, (_r, e) in enumerate(buf):
                    dst = dsts[idx] if ok and idx < len(dsts) else ""
                    good = bool(dst)
                    why = errs[idx] if idx < len(errs) and errs[idx] else (err or "转存失败")
                    self.store.run(
                        "INSERT INTO items(task_id,src,size,digest,dst,status,error) VALUES(?,?,?,?,?,?,?)"
                        " ON CONFLICT(task_id,src) DO UPDATE SET status=excluded.status,dst=excluded.dst,error=excluded.error",
                        (task_id, e.path, e.size, e.digest, dst, "done" if good else "failed",
                         "" if good else why),
                    )
                    if good:
                        done += 1
                    else:
                        failed += 1
                buf.clear()
                self.store.run(
                    "UPDATE tasks SET done=?,failed=?,updated_at=? WHERE id=?",
                    (done, failed, now(), task_id),
                )

            for ref in refs:
                if self._is_paused(task_id) or self._is_canceled(task_id):
                    return
                self._set_status(task_id, "listing")
                share_start = time.time()
                gen = prov.list_share(ref)
                try:
                    for entry in gen:
                        seen += 1
                        if self._is_paused(task_id) or self._is_canceled(task_id):
                            return
                        # 单条分享给足 30 分钟：大分享能跑完，真无响应时单次请求
                        # 本身有 20s 超时 + 重试，会先抛出来
                        if time.time() - share_start > SHARE_MAX:
                            raise ProviderError(
                                f"单条分享处理超过 {SHARE_MAX // 60} 分钟仍未完成"
                                f"（已发现 {seen} 个文件），点「重试」可从中断处继续"
                            )
                        self.store.run(
                            "UPDATE tasks SET total=?,updated_at=? WHERE id=?", (seen, now(), task_id)
                        )
                        if entry.path in done_set:
                            continue
                        buf.append((ref, entry))
                        if len(buf) >= BATCH_SIZE:
                            flush_batch()
                finally:
                    closer = getattr(gen, "close", None)
                    if closer:
                        closer()
            flush_batch()
            if not seen:
                raise ProviderNotReady(
                    "分享内没有可用文件（若分享里确有内容，请到「协议诊断」用同一链接查看原始响应）"
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

    def _batch_with_retry(self, prov, ref, entries: list, target: str,
                          cfg: dict | None = None) -> tuple[bool, list, str]:
        """整批转存；provider 不支持批量时退化为逐个。"""
        last = ""
        attempts = max(1, int((cfg or {}).get("retries") or MAX_ATTEMPTS))
        batch_fn = getattr(prov, "transfer_batch", None)
        for attempt in range(attempts):
            try:
                if batch_fn:
                    return True, list(batch_fn(ref, entries, target)), ""
                out = []
                for e in entries:
                    ok, dst, err = self._transfer_with_retry(prov, ref, e, target, cfg)
                    if not ok:
                        return False, out, err
                    out.append(dst)
                return True, out, ""
            except AuthExpired as e:
                return False, [], f"登录态失效：{e}"
            except ProviderError as e:
                last = str(e)
                time.sleep(min(2 ** attempt, 5) * 0.3)
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                break
        return False, [], last

    def _transfer_with_retry(self, prov, ref, entry, target: str, cfg: dict | None = None) -> tuple[bool, str, str]:
        last = ""
        attempts = max(1, int((cfg or {}).get("retries") or MAX_ATTEMPTS))
        for attempt in range(attempts):
            try:
                dst = prov.transfer(ref, entry, target)
                return True, dst, ""
            except AuthExpired as e:
                return False, "", f"登录态失效：{e}"
            except ProviderError as e:
                last = str(e)
                time.sleep(min(2 ** attempt, 5) * 0.3)
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                break
        return False, "", last

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
