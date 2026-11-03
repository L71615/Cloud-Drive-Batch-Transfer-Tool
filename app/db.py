# -*- coding: utf-8 -*-
"""极简 SQLite 仓储。表结构为自建，与任何既有软件的 schema 无关。"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT    NOT NULL,
    name        TEXT    NOT NULL DEFAULT '',
    credential  TEXT    NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks(
    id          TEXT    PRIMARY KEY,
    provider    TEXT    NOT NULL,
    target      TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL,
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    error       TEXT    NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS items(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT    NOT NULL,
    src         TEXT    NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    digest      TEXT    NOT NULL DEFAULT '',
    dst         TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL,
    error       TEXT    NOT NULL DEFAULT '',
    UNIQUE(task_id, src)
);

CREATE INDEX IF NOT EXISTS idx_items_task ON items(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


def now() -> int:
    return int(time.time())


class Store:
    """每线程一个连接，WAL 模式，写操作串行化交给 SQLite。"""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._local = threading.local()
        self.conn().executescript(SCHEMA)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=20, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.c = c
        return c

    # --- 查询辅助 ---
    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        r = self.conn().execute(sql, args).fetchone()
        return dict(r) if r else None

    def run(self, sql: str, args: tuple = ()) -> None:
        self.conn().execute(sql, args)

    def tx(self, fn):
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            fn(c)
        except Exception:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")

    def close(self) -> None:
        c = getattr(self._local, "c", None)
        if c is not None:
            c.close()
            self._local.c = None
