# -*- coding: utf-8 -*-
"""端到端流程测试：解析 -> 列表 -> 转存 -> 终态。仅用 mock provider。"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Store
from app.providers import create, names
from app.providers.base import ProviderNotReady
from app.providers.mock import MockProvider
from app.tasks import TaskRunner


class TestParse(unittest.TestCase):
    def test_mock_dedup(self):
        p = create("mock", "")
        refs = p.parse_links("a a b")
        self.assertEqual([r.share_id for r in refs][0], refs[0].share_id)
        self.assertEqual(len(refs), 2)

    def test_baidu_link(self):
        p = create("baidu", "")
        refs = p.parse_links("看看 https://pan.baidu.com/s/1AbCdEf?pwd=x9y8 提取码: x9y8")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].share_id, "1AbCdEf")

    def test_quark_and_uc(self):
        q = create("quark", "").parse_links("https://pan.quark.cn/s/abcdef123456")
        u = create("uc", "").parse_links("https://drive.uc.cn/s/zyxwvu987654")
        self.assertEqual(len(q), 1)
        self.assertEqual(len(u), 1)

    def test_registry(self):
        self.assertEqual(names(), ["baidu", "mock", "quark", "uc"])


class TestTaskFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pt-test-")
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.runner = TaskRunner(self.store, workers=2)

    def tearDown(self):
        self.runner.shutdown()
        self.store.close()

    def _wait(self, task_id, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            v = self.runner.view(task_id)
            if v and v["status"] in ("done", "failed", "canceled"):
                return v
            time.sleep(0.05)
        self.fail("task did not finish in time")

    def test_mock_task_done(self):
        tid = self.runner.create_task("mock", "alpha beta", "/dst")
        v = self._wait(tid)
        self.assertEqual(v["status"], "done", v.get("error"))
        self.assertGreater(v["total"], 0)
        self.assertEqual(v["done"] + v["failed"], v["total"])
        self.assertTrue(all(i["dst"].startswith("/dst/") for i in v["items"]))

    def test_real_provider_not_ready(self):
        tid = self.runner.create_task("baidu", "https://pan.baidu.com/s/1AbCdEf", "/dst")
        v = self._wait(tid)
        self.assertEqual(v["status"], "failed")
        self.assertIn("baidu.share_info", v["error"])

    def test_empty_links_fails(self):
        tid = self.runner.create_task("mock", "   ", "/dst")
        v = self._wait(tid)
        self.assertEqual(v["status"], "failed")

    def test_pause_then_resume(self):
        tid = self.runner.create_task("mock", "a b c d e f g h", "/dst")
        self.runner.pause(tid)
        time.sleep(0.3)
        self.assertIn(self.runner.view(tid)["status"], ("paused", "done"))
        if self.runner.view(tid)["status"] == "paused":
            self.runner.resume(tid)
            v = self._wait(tid)
            self.assertIn(v["status"], ("done", "failed"))


class TestStore(unittest.TestCase):
    def test_schema_roundtrip(self):
        tmp = tempfile.mkdtemp(prefix="pt-store-")
        s = Store(os.path.join(tmp, "x.db"))
        s.run("INSERT INTO accounts(provider,name,credential,created_at) VALUES(?,?,?,?)",
              ("mock", "n", "c", 1))
        self.assertEqual(s.one("SELECT provider FROM accounts")["provider"], "mock")
        s.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
