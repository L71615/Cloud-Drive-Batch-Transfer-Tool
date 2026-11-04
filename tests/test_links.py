# -*- coding: utf-8 -*-
"""链接识别相关的单元测试：容易漏的复制粘贴残留与老式链接。"""
from __future__ import annotations

import unittest

from app.providers import create
from app.providers.base import clean_text


class TestCleanText(unittest.TestCase):
    def test_strips_zero_width_and_nbsp(self):
        raw = "https://pan.quark.cn/s/\u200babcd1234\ufeff"
        self.assertEqual(clean_text(raw), "https://pan.quark.cn/s/abcd1234")

    def test_fullwidth_delimiters(self):
        self.assertEqual(clean_text("a\uff1ab\uff1fc"), "a:b?c")

    def test_none(self):
        self.assertEqual(clean_text(None), "")


class TestLinkForms(unittest.TestCase):
    def test_quark_link_with_zero_width_paste(self):
        prov = create("quark", "")
        refs = prov.parse_links("分享给你 https://pan.quark.cn/s/\u200babc123def456 提取码：9a8b")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].share_id, "abc123def456")
        self.assertEqual(refs[0].passcode, "9a8b")

    def test_quark_drive_pc_host(self):
        prov = create("quark", "")
        refs = prov.parse_links("https://drive-pc.quark.cn/s/abc123def456")
        self.assertEqual([r.share_id for r in refs], ["abc123def456"])

    def test_uc_pc_api_host(self):
        prov = create("uc", "")
        refs = prov.parse_links("https://pc-api.uc.cn/s/aaaaaaaabbbb")
        self.assertEqual([r.share_id for r in refs], ["aaaaaaaabbbb"])

    def test_baidu_yun_host(self):
        prov = create("baidu", "")
        refs = prov.parse_links("https://yun.baidu.com/s/1AbCdEf 提取码: x9y8")
        self.assertEqual([r.share_id for r in refs], ["1AbCdEf"])

    def test_baidu_share_init_surl(self):
        """老式 pan.baidu.com/share/init?surl=AbCdEf，接口要带前导 1。"""
        prov = create("baidu", "")
        refs = prov.parse_links("https://pan.baidu.com/share/init?surl=AbCdEf&pwd=x9y8")
        self.assertEqual([r.share_id for r in refs], ["1AbCdEf"])

    def test_cross_provider_detection(self):
        """一条夸克链接在百度 provider 下认不出来，但夸克能认出来 —— 界面据此提示切换。"""
        text = "https://pan.quark.cn/s/abc123def456"
        self.assertEqual(create("baidu", "").parse_links(text), [])
        self.assertEqual(len(create("quark", "").parse_links(text)), 1)


if __name__ == "__main__":
    unittest.main()
