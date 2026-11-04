"""登录态清洗与校验（纯函数，无网络）。"""

import unittest

from app.credentials import missing, normalize, parse, validate


class CredentialTest(unittest.TestCase):
    def test_normalize_strips_cookie_prefix(self):
        raw = "Cookie: BDUSS=abc; STOKEN=def\n"
        out = normalize(raw)
        self.assertEqual(out, "BDUSS=abc; STOKEN=def")

    def test_normalize_keeps_multiline(self):
        raw = "BDUSS=abc\r\nSTOKEN=def\r\n\r\n__pus=ghi"
        out = normalize(raw)
        self.assertIn("BDUSS=abc", out)
        self.assertIn("__pus=ghi", out)
        self.assertNotIn("\n", out)

    def test_parse_fields(self):
        d = parse("BDUSS=a=b; STOKEN=c")
        self.assertEqual(d.get("BDUSS"), "a=b")

    def test_baidu_requires_bduss(self):
        self.assertEqual(missing("baidu", "STOKEN=only"), ["BDUSS"])
        self.assertEqual(missing("baidu", "BDUSS=x"), [])

    def test_quark_accepts_either_pus(self):
        self.assertEqual(missing("quark", "__puus=x"), [])
        self.assertEqual(missing("quark", "__pus=x"), [])
        gaps = missing("quark", "other=1")
        self.assertEqual(len(gaps), 1)
        self.assertIn("__pus", gaps[0])
        self.assertIn("__puus", gaps[0])

    def test_validate_rejects_empty(self):
        v = validate("baidu", "   ")
        self.assertFalse(v["ok"])
        self.assertTrue(v["error"])

    def test_validate_rejects_garbage(self):
        v = validate("baidu", "这不是cookie")
        self.assertFalse(v["ok"])

    def test_validate_ok_returns_clean_cookie(self):
        v = validate("baidu", "Cookie: BDUSS=abc; STOKEN=def")
        self.assertTrue(v["ok"])
        self.assertEqual(v["cookie"], "BDUSS=abc; STOKEN=def")
        self.assertEqual(v["count"], 2)

    def test_validate_missing_key_message(self):
        v = validate("uc", "foo=bar")
        self.assertFalse(v["ok"])
        self.assertIn("__pus", v["error"])


if __name__ == "__main__":
    unittest.main()
