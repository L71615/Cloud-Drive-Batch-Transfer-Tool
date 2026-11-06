# -*- coding: utf-8 -*-
"""文件名与目录规范化的单元测试。"""
import unittest

from app.names import clean_segment, limit_name, name_problem, normalize_dir, split_ext


class NamesTest(unittest.TestCase):
    def test_normalize_dir_basic(self):
        self.assertEqual(normalize_dir("/a/b"), "/a/b")
        self.assertEqual(normalize_dir("/a//b/"), "/a/b")
        self.assertEqual(normalize_dir("a/b"), "/a/b")
        self.assertEqual(normalize_dir("  /a/b/  "), "/a/b")
        self.assertEqual(normalize_dir("\\a\\b"), "/a/b")

    def test_normalize_dir_defaults(self):
        self.assertEqual(normalize_dir(""), "/PanTransfer")
        self.assertEqual(normalize_dir(None), "/PanTransfer")
        self.assertEqual(normalize_dir("/"), "/")

    def test_normalize_dir_rejects_dotdot(self):
        with self.assertRaises(ValueError):
            normalize_dir("/a/../b")

    def test_normalize_dir_cleans_illegal(self):
        self.assertEqual(normalize_dir("/a<b>/c"), "/a_b_/c")

    def test_clean_segment(self):
        self.assertEqual(clean_segment("a.txt"), "a.txt")
        self.assertEqual(clean_segment("a:b"), "a_b")
        self.assertEqual(clean_segment("a/ b"), "a_ b")
        self.assertEqual(clean_segment("name. "), "name")     # 结尾的点和空格要去掉
        self.assertEqual(clean_segment("CON"), "_CON")        # Windows 保留名
        self.assertEqual(clean_segment(""), "_")
        self.assertEqual(clean_segment("a\x00b"), "ab")       # 控制字符

    def test_split_ext(self):
        self.assertEqual(split_ext("a.mp4"), ("a", ".mp4"))
        self.assertEqual(split_ext(".bashrc"), (".bashrc", ""))
        self.assertEqual(split_ext("noext"), ("noext", ""))
        self.assertEqual(split_ext("a."), ("a.", ""))

    def test_limit_name_keeps_extension(self):
        long = "x" * 400 + ".mp4"
        out = limit_name(long, 100)
        self.assertEqual(len(out), 100)
        self.assertTrue(out.endswith(".mp4"), out[-10:])

    def test_limit_name_short_unchanged(self):
        self.assertEqual(limit_name("a.mp4", 100), "a.mp4")

    def test_name_problem(self):
        self.assertEqual(name_problem("正常.mp4"), "")
        self.assertEqual(name_problem(""), "文件名为空")
        self.assertIn("超过网盘上限", name_problem("x" * 400))
        # 网盘允许而 Windows 不允许的字符不该被拦——夸克/UC 上确实存在这种名字
        self.assertEqual(name_problem("a:b?.mp4"), "")

    def test_name_problem_limit_boundary(self):
        self.assertEqual(name_problem("x" * 250, 250), "")
        self.assertIn("251", name_problem("x" * 251, 250))


if __name__ == "__main__":
    unittest.main()
