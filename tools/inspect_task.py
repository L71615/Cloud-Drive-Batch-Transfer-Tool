# -*- coding: utf-8 -*-
"""临时排查脚本：看某个任务为什么大面积失败。"""
import sqlite3
import sys

TASK = sys.argv[1] if len(sys.argv) > 1 else "742d21fcf671"
c = sqlite3.connect(r"E:\设备转存软件\pantransfer\data\pantransfer.db")
c.row_factory = sqlite3.Row

t = c.execute("SELECT * FROM tasks WHERE id=?", (TASK,)).fetchone()
print("task:", dict(t) if t else "not found")

print("items:", c.execute("SELECT count(*) FROM items WHERE task_id=?", (TASK,)).fetchone()[0])
for r in c.execute("SELECT status, count(*) n FROM items WHERE task_id=? GROUP BY status", (TASK,)):
    print("  ", r["status"], r["n"])

print("--- 失败样本 ---")
for r in c.execute(
    "SELECT src, status, error FROM items WHERE task_id=? AND status<>'done' LIMIT 8", (TASK,)
):
    print("  ", r["src"], "|", r["error"])

print("--- 错误归类 ---")
for r in c.execute(
    "SELECT error, count(*) n FROM items WHERE task_id=? AND status<>'done' GROUP BY error ORDER BY n DESC LIMIT 10",
    (TASK,),
):
    print("  ", r["n"], "x", r["error"])
