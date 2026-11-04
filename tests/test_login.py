"""浏览器代登录的集成自检：真开一个（无头）浏览器，种 Cookie 再读回来。

没有浏览器时整组跳过，CI 上不会失败。
"""

import os
import time
import unittest

from app import browser_login as bl


def _has_browser() -> bool:
    try:
        bl.find_browser()
        return True
    except bl.BrowserLoginError:
        return False


@unittest.skipUnless(_has_browser(), "本机没有 Edge/Chrome")
class BrowserLoginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["PANTRANSFER_LOGIN_HEADLESS"] = "1"

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("PANTRANSFER_LOGIN_HEADLESS", None)
        bl.MANAGER.close_all()

    def test_waiting_then_ready(self):
        view = bl.MANAGER.start("baidu")
        sid = view["sid"]
        self.assertIn(view["status"], ("starting", "waiting"))
        sess = bl.MANAGER.get(sid)
        self.assertIsNotNone(sess)
        try:
            # 等调试端口就绪，然后模拟"用户在官方页面登录成功"
            ws, _ = bl._page_target(sess.port)
            cdp = bl._CDP(ws)
            cdp.call("Network.enable")
            cdp.call("Network.setCookie", {
                "name": "BDUSS", "value": "integration", "domain": ".baidu.com", "path": "/",
            })
            cdp.close()

            deadline = time.time() + 15
            while time.time() < deadline:
                got = sess.poll()
                if got["status"] == "ready":
                    break
                time.sleep(0.4)
            self.assertEqual(got["status"], "ready", got.get("message"))
            self.assertIn("BDUSS=integration", got["cookie"])
            self.assertGreaterEqual(got["count"], 1)
        finally:
            bl.MANAGER.close(sid)
            self.assertIsNone(bl.MANAGER.get(sid))

    def test_unknown_provider_rejected(self):
        with self.assertRaises(bl.BrowserLoginError):
            bl.MANAGER.start("dropbox")


if __name__ == "__main__":
    unittest.main()
