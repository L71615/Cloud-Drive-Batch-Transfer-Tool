# -*- coding: utf-8 -*-
"""自适应限速：被限速要降、顺利要升、上限不被突破。"""
import unittest

from app.ratelimit import FLOOR, REWARD_EVERY, TokenBucket, bucket


class RateLimitTest(unittest.TestCase):
    def test_starts_below_ceiling(self):
        b = TokenBucket(10.0)
        self.assertEqual(b.ceiling, 10.0)
        self.assertLess(b.rate, b.ceiling, "起步不该直接顶到上限")
        self.assertGreaterEqual(b.rate, FLOOR)

    def test_penalize_halves_and_recovers(self):
        b = TokenBucket(8.0)
        start = b.rate
        b.penalize()
        self.assertLess(b.rate, start)
        self.assertEqual(b.penalties, 1)
        for _ in range(REWARD_EVERY * 12):
            b.reward()
        self.assertLessEqual(b.rate, b.ceiling, "恢复也不能超过上限")

    def test_floor_is_respected(self):
        b = TokenBucket(4.0)
        for _ in range(30):
            b.penalize()
        self.assertGreaterEqual(b.rate, FLOOR, "降速不能降到停摆")

    def test_ceiling_change_applies(self):
        b = bucket("test:ceiling", 10.0)
        bucket("test:ceiling", 5.0)
        self.assertEqual(b.ceiling, 5.0)
        self.assertLessEqual(b.rate, 5.0)

    def test_penalize_with_retry_after_delays(self):
        b = TokenBucket(5.0)
        b.penalize(retry_after=1.0)
        self.assertFalse(b.acquire(1.0, timeout=0.05), "被罚站期间拿不到令牌")


if __name__ == "__main__":
    unittest.main()
