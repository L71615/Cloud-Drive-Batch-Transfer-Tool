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
        entries = p.list_share(ref)
        paths = sorted(e.path for e in entries)
        self.assertEqual(paths, ["/a.txt", "/b.bin", "/相册/c.txt"], "应递归展开子目录")

        dst = p.transfer(ref, entries[0], "/PanTransfer")
        self.assertEqual(dst, "/PanTransfer/a.txt")
        quota = p.quota()
        self.assertGreater(quota["total"], 0)

    def test_baidu_wrong_passcode(self):
        p = self._p(BaiduProvider)
        ref = p.parse_links("https://pan.baidu.com/s/1AbCdEf 提取码: 0000")[0]
        with self.assertRaises(ProviderError):
            p.list_share(ref)

    def test_baidu_bad_cookie(self):
        p = self._p(BaiduProvider, cookie="BDUSS=BAD")
        with self.assertRaises(AuthExpired):
            p.list_dir("/")
        self.assertFalse(p.check_auth()["valid"])

    # ---------------- 夸克 ----------------
    def test_quark_full_flow(self):
        p = self._p(QuarkProvider, cookie="__pus=abc")
        ref = p.parse_links("https://pan.quark.cn/s/a1b2c3d4e5f6")[0]
        entries = p.list_share(ref)
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
            p.list_share(ref)

    # ---------------- UC ----------------
    def test_uc_parse_and_list(self):
        p = self._p(UcProvider, cookie="__pus=abc")
        refs = p.parse_links("https://drive.uc.cn/s/a1b2c3d4e5f6 提取码: 9a8b\nhttps://fast.uc.cn/s/a1b2c3d4e5f6")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].passcode, "9a8b")
        entries = p.list_share(refs[0])
        self.assertIn("/相册/c.txt", [e.path for e in entries])

    def test_uc_bad_cookie(self):
        p = self._p(UcProvider, cookie="BAD")
        with self.assertRaises(AuthExpired):
            p.list_dir("0")


if __name__ == "__main__":
    unittest.main()
