# -*- coding: utf-8 -*-
"""适配器离线联调：把三个 provider 指向本地假网盘，跑完整链路。

覆盖：链接解析 → 分享 token/校验 → 递归列目录 → 目录创建 → 转存（含异步轮询）
     → 容量查询 → 登录态检测失败路径。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.providers.baidu import BaiduProvider          # noqa: E402
from app.providers.base import AuthExpired, ProviderError  # noqa: E402
from app.providers.quark import QuarkProvider          # noqa: E402
from app.providers.uc import UcProvider                # noqa: E402
from tools.mock_netdisk import serve                   # noqa: E402


class AdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.srv = serve(0)
        port = cls.srv.server_address[1]
        cls.base = f"http://127.0.0.1:{port}"
        cls._orig = (BaiduProvider.BASE, QuarkProvider.BASE, UcProvider.BASE)
        BaiduProvider.BASE = cls.base + "/baidu"
        QuarkProvider.BASE = cls.base + "/cd"
        UcProvider.BASE = cls.base + "/cd"

    @classmethod
    def tearDownClass(cls) -> None:
        BaiduProvider.BASE, QuarkProvider.BASE, UcProvider.BASE = cls._orig
        cls.srv.shutdown()
        cls.srv.server_close()

    def _p(self, cls, cookie: str = "BDUSS=abc; STOKEN=def", qps: float = 50.0):
        p = cls(cookie)
        p.qps = qps
        return p

    # ---------------- 百度 ----------------
    def test_baidu_parse_with_passcode(self):
        p = self._p(BaiduProvider)
        refs = p.parse_links("https://pan.baidu.com/s/1AbCdEf 提取码: x9y8\nhttps://pan.baidu.com/s/1AbCdEf")
        self.assertEqual(len(refs), 1, "同一个 share_id 应去重")
        self.assertEqual(refs[0].share_id, "1AbCdEf")
        self.assertEqual(refs[0].passcode, "x9y8")

    def test_baidu_full_flow(self):
        p = self._p(BaiduProvider)
        ref = p.parse_links("https://pan.baidu.com/s/1AbCdEf 提取码: x9y8")[0]
        entries = list(p.list_share(ref))
        paths = sorted(e.path for e in entries)
        self.assertEqual(paths, ["/a.txt", "/b.bin", "/相册/c.txt"], "应递归展开子目录")

        dst = p.transfer(ref, entries[0], "/PanTransfer")
        self.assertEqual(dst, "/PanTransfer/a.txt")
        quota = p.quota()
        self.assertGreater(quota["total"], 0)

    def test_baidu_batch_transfer(self):
        """一次 save 提交多个 fs_id：批量转存应返回每条的落盘路径，且都真实可见。"""
        p = self._p(BaiduProvider)
        ref = p.parse_links("https://pan.baidu.com/s/1AbCdEf 提取码: x9y8")[0]
        entries = list(p.list_share(ref))
        dsts = p.transfer_batch(ref, entries, "/批量")
        self.assertEqual(len(dsts), len(entries))
        self.assertEqual(sorted(x.rsplit("/", 1)[-1] for x in dsts), ["a.txt", "b.bin", "c.txt"])
        names = sorted(x["name"] for x in p.browse("/批量"))
        self.assertEqual(names, ["a.txt", "b.bin", "c.txt"], "批量转存的文件应真的落到目标目录")

    def test_quark_batch_transfer(self):
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/a1b2c3d4e5f6")[0]
        entries = list(p.list_share(ref))
        dsts = p.transfer_batch(ref, entries, "/批量")
        self.assertEqual(sorted(x.rsplit("/", 1)[-1] for x in dsts), ["a.txt", "c.txt"])

    def test_baidu_wrong_passcode(self):
        p = self._p(BaiduProvider)
        ref = p.parse_links("https://pan.baidu.com/s/1AbCdEf 提取码: 0000")[0]
        with self.assertRaises(ProviderError):
            list(p.list_share(ref))

    def test_baidu_bad_cookie(self):
        p = self._p(BaiduProvider, cookie="BDUSS=BAD")
        with self.assertRaises(AuthExpired):
            p.list_dir("/")
        self.assertFalse(p.check_auth()["valid"])

    # ---------------- 夸克 ----------------
    def test_quark_full_flow(self):
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/a1b2c3d4e5f6")[0]
        entries = list(p.list_share(ref))
        self.assertEqual(sorted(e.path for e in entries), ["/a.txt", "/相册/c.txt"])
        self.assertTrue(all(e.meta.get("token") for e in entries), "每条都要带 share token")

        dst = p.transfer(ref, entries[0], "/PanTransfer")
        self.assertEqual(dst, "/PanTransfer/a.txt")
        self.assertGreaterEqual(p.quota()["total"], 0)

    def test_quark_wrong_passcode(self):
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/zyxwvu987654")[0]
        ref.passcode = "0000"
        with self.assertRaises(ProviderError):
            list(p.list_share(ref))

    # ---------------- UC ----------------
    def test_uc_parse_and_list(self):
        p = self._p(UcProvider, cookie="__pus=abc")
        refs = p.parse_links("https://drive.uc.cn/s/a1b2c3d4e5f6 提取码: 9a8b\nhttps://fast.uc.cn/s/a1b2c3d4e5f6")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].passcode, "9a8b")
        entries = list(p.list_share(refs[0]))
        self.assertIn("/相册/c.txt", [e.path for e in entries])

    def test_uc_bad_cookie(self):
        p = self._p(UcProvider, cookie="BAD")
        with self.assertRaises(AuthExpired):
            p.list_dir("0")

    # ---------------- 目录浏览 / 协议诊断 ----------------
    def test_baidu_browse_after_transfer(self):
        p = self._p(BaiduProvider)
        ref = p.parse_links("https://pan.baidu.com/s/1AbCdEf 提取码: x9y8")[0]
        entries = list(p.list_share(ref))
        p.transfer(ref, entries[0], "/浏览测试")
        top = [x["name"] for x in p.browse("/")]
        self.assertIn("浏览测试", top)
        self.assertTrue(all(x["is_dir"] for x in p.browse("/") if x["name"] == "浏览测试"))
        sub = p.browse("/浏览测试")
        self.assertEqual([x["name"] for x in sub], ["a.txt"])
        self.assertFalse(sub[0]["is_dir"])
        self.assertEqual(sub[0]["path"], "/浏览测试/a.txt")

    def test_quark_browse_after_transfer(self):
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/a1b2c3d4e5f6")[0]
        entries = list(p.list_share(ref))
        p.transfer(ref, entries[0], "/浏览测试")
        top = [x["name"] for x in p.browse("/")]
        self.assertIn("浏览测试", top)
        self.assertEqual([x["name"] for x in p.browse("/浏览测试")], ["a.txt"])

    def test_quark_browse_missing_dir(self):
        p = self._p(QuarkProvider, cookie="__pus=abc")
        with self.assertRaises(ProviderError):
            p.browse("/不存在的目录")

    def test_baidu_diagnose_steps(self):
        p = self._p(BaiduProvider)
        ref = p.parse_links("https://pan.baidu.com/s/1AbCdEf 提取码: x9y8")[0]
        steps = p.diagnose(ref)
        self.assertEqual(len(steps), 4)
        self.assertTrue(all(s["ok"] for s in steps), steps)
        self.assertIn("总", steps[-1]["detail"])

    def test_quark_transfer_batch_mixed_dirs(self):
        """子目录里的文件必须带自己的 pdir_fid，否则服务端会回「token校验异常」。

        假网盘会像真服务端一样校验 fid 与 pdir_fid 同源、token 与 fid 配对。
        """
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/a1b2c3d4e5f6")[0]
        entries = list(p.list_share(ref))
        names = sorted(e.path for e in entries)
        self.assertEqual(names, ["/a.txt", "/相册/c.txt"], "应递归展开子目录")
        dsts = p.transfer_batch(ref, entries, "/混合目录")
        self.assertEqual(len(dsts), 2)
        self.assertTrue(all(dsts), f"两条都应转存成功：{dsts}")
        # 落点保留分享内的目录结构：/a.txt 在根，/相册/c.txt 落到 /混合目录/相册/
        self.assertEqual(sorted(dsts), ["/混合目录/a.txt", "/混合目录/相册/c.txt"])
        top = sorted(x["name"] for x in p.browse("/混合目录"))
        self.assertEqual(top, ["a.txt", "相册"])
        sub = sorted(x["name"] for x in p.browse("/混合目录/相册"))
        self.assertEqual(sub, ["c.txt"])

    def test_quark_save_rejects_query_fid_as_pdir(self):
        """回归：save 的 pdir_fid 传列目录用的相对 fid（根为 "0"）必须被拒。

        真实 UC/夸克会回 403「转存文件token校验异常」，假网盘照此校验，
        这样将来谁再把 meta["pdir"] 写回查询 fid，测试会立刻红。
        """
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/a1b2c3d4e5f6")[0]
        entries = list(p.list_share(ref))
        self.assertTrue(entries)
        dest = p.ensure_dir("/坏Pdir")
        with self.assertRaises(ProviderError) as ctx:
            p._save_group(ref, p.share_token(ref), "0", [entries[0]], dest)
        self.assertIn("token", str(ctx.exception))

    def test_quark_diagnose_reports_failure(self):
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/zyxwvu987654")[0]
        ref.passcode = "0000"
        steps = p.diagnose(ref)
        self.assertEqual(len(steps), 4, "失败的步骤不能中断后面的步骤")
        self.assertTrue(steps[0]["ok"])
        self.assertFalse(steps[1]["ok"], "第二步取 token 应失败")
        self.assertIn("error", steps[1])


    def _wait(self, runner, tid: str, timeout: float = 25.0) -> dict:
        import time
        end = time.time() + timeout
        while time.time() < end:
            v = runner.view(tid)
            if v["status"] in ("done", "failed", "canceled"):
                return v
            time.sleep(0.1)
        return runner.view(tid)

    def test_task_skips_existing_names(self):
        """Run the same share into the same folder twice: nothing should be duplicated."""
        import os
        import tempfile
        from app.db import Store
        from app.tasks import TaskRunner

        tmp = tempfile.mkdtemp()
        store = Store(os.path.join(tmp, "t.db"))
        runner = TaskRunner(store, workers=1)
        cred = "__pus=abc"
        link = "https://pan.quark.cn/s/a1b2c3d4e5f6"
        try:
            tid = runner.create_task("quark", link, "/Dedup", cred)
            first = self._wait(runner, tid)
            self.assertEqual(first["status"], "done", first.get("error"))
            n1 = first["done"]
            self.assertGreaterEqual(n1, 2)

            tid2 = runner.create_task("quark", link, "/Dedup", cred)
            second = self._wait(runner, tid2)
            self.assertEqual(second["status"], "done", second.get("error"))
            # 第二次全部命中"同名同大小"，记为跳过而不是 done——数字要诚实
            self.assertEqual(second["done"], 0, "nothing new should be written")
            self.assertEqual(second["skipped"], n1, "all files should be counted as skipped")
            self.assertEqual(second["failed"], 0)

            p = self._p(QuarkProvider, cookie=cred)
            names = [x["name"] for x in p.browse("/Dedup")]
            self.assertFalse([n for n in names if "(" in n], f"duplicated files: {names}")
        finally:
            runner.shutdown()
            store.close()


if __name__ == "__main__":
    unittest.main()
