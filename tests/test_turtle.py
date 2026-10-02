# -*- coding: utf-8 -*-
"""
turtle.py 계산 로직 검증 — 가짜 일봉, 인터넷 없음.

  python3 -m unittest tests.test_turtle -v
"""
import os, sys, math, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import turtle  # noqa: E402


def bar(i, o, h, l, c):
    return {"date": "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), "o": o, "h": h, "l": l, "c": c}


def flat(n, px=100.0, rng=2.0):
    """n 일 동안 px 근처에서 횡보 (고 px+rng, 저 px-rng)."""
    return [bar(i, px, px + rng, px - rng, px) for i in range(n)]


class Indicators(unittest.TestCase):
    def test_wilder_atr(self):
        bars = flat(30)
        atr = turtle.wilder_atr(bars)
        self.assertIsNone(atr[turtle.ATR_N - 2])
        self.assertAlmostEqual(atr[turtle.ATR_N - 1], 4.0)     # TR = 고−저 = 4
        self.assertAlmostEqual(atr[-1], 4.0)

    def test_channel(self):
        bars = flat(25)
        bars[10]["h"] = 150.0
        self.assertEqual(turtle.channel(bars, 25, 20, "h", max), 150.0)   # i 자신은 제외, 직전 20개
        self.assertEqual(turtle.channel(bars, 25, 10, "h", max), 102.0)
        self.assertIsNone(turtle.channel(bars, 5, 20, "h", max))


class Backtest(unittest.TestCase):
    def _trend(self):
        """40일 횡보 → 돌파 → 10일 상승 → 10일 최저가 이탈."""
        bars = flat(40)
        n = len(bars)
        bars.append(bar(n, 100, 110, 99, 109))            # 20일 최고가(102) 돌파 → max(시가 100, 102) 체결
        for k in range(1, 11):
            px = 109 + k * 3
            bars.append(bar(n + k, px - 1, px + 2, px - 2, px))     # 상승
        last = bars[-1]["c"]
        for k in range(1, 4):                                     # 급락 → 10일 최저가 이탈
            bars.append(bar(n + 10 + k, last - 5 * k, last - 5 * k + 1, last - 5 * k - 30, last - 5 * k - 20))
        return bars

    def test_long_breakout_entry_and_sizing(self):
        bars = self._trend()
        res = turtle.run({"X": bars}, False, 0.0, 10_000.0, slip_pct=0.0)
        self.assertEqual(len(res["trades"]), 1)
        t = res["trades"][0]
        self.assertEqual(t["dir"], "long")
        self.assertAlmostEqual(t["entry"], 102.0)                      # 돌파가 체결
        self.assertAlmostEqual(t["n_at_entry"], 4.0)
        self.assertAlmostEqual(t["units"], 10_000 * 0.01 / 4.0)        # 계좌 1% ÷ N
        self.assertAlmostEqual(t["stop"], 102.0 - 2 * 4.0)             # 진입가 − 2N
        self.assertEqual(t["reason"], "channel")
        self.assertGreater(t["pnl"], 0)
        self.assertAlmostEqual(math.prod(1 + x["ret_pct"] / 100 for x in res["trades"]) * 10_000, res["equity"], places=6)

    def test_stop_loss_at_2n(self):
        bars = flat(40)
        n = len(bars)
        bars.append(bar(n, 100, 110, 99, 109))        # 진입 102, N=4, 손절 94
        bars.append(bar(n + 1, 108, 109, 80, 85))     # 폭락: 손절 94 > 10일 최저가 98? 아니 — 10일 최저 98 이 더 높다
        res = turtle.run({"X": bars}, False, 0.0, 10_000.0, slip_pct=0.0)
        t = res["trades"][0]
        # 청산 기준은 손절(94)과 10일 최저가(98) 중 높은 쪽 → 98 에서 나간다
        self.assertAlmostEqual(t["exit"], 98.0)
        self.assertEqual(t["reason"], "channel")
        # 손절이 더 높은 경우: N 을 작게 만들어 본다
        bars2 = flat(40, rng=0.5)                      # N = 1
        bars2.append(bar(40, 100, 110, 99.5, 109))     # 진입 100.5, 손절 98.5 > 10일 최저 99.5? 아니 99.5 가 더 높다
        bars2.append(bar(41, 105, 106, 99.6, 100))     # 저가 99.6: 둘 다 안 걸림
        bars2.append(bar(42, 100, 100.5, 99.0, 99.2))  # 저가 99.0 ≤ 99.5 → 채널 청산
        res2 = turtle.run({"X": bars2}, False, 0.0, 10_000.0, slip_pct=0.0)
        self.assertEqual(res2["trades"][0]["exit"], 99.5)
        # 손절이 기준이 되는 경우: 10일 최저가가 손절보다 낮을 때뿐이다. 보유 중 그런 저가가 찍히면 그 자리에서
        # 이미 청산되므로, 실제로는 진입 당일 봉의 저가가 손절 아래였던 경우(큰 범위 봉)에만 손절이 먼저 걸린다.
        bars3 = flat(40)
        bars3.append(bar(40, 100, 110, 90, 109))                       # 진입 102, 손절 94, 당일 저가 90
        bars3.append(bar(41, 108, 109, 93.0, 95))                      # 10일 최저가 90 < 손절 94 → 94 에서 손절
        res3 = turtle.run({"X": bars3}, False, 0.0, 10_000.0, slip_pct=0.0)
        self.assertEqual(len(res3["trades"]), 1)
        self.assertEqual(res3["trades"][0]["reason"], "stop")
        self.assertAlmostEqual(res3["trades"][0]["exit"], 94.0)

    def test_gap_fills_at_open(self):
        bars = flat(40)
        bars.append(bar(40, 120, 125, 118, 121))         # 갭 상승 시가 120 > 돌파가 102 → 120 체결
        res = turtle.run({"X": bars}, False, 0.0, 10_000.0, slip_pct=0.0)
        self.assertAlmostEqual(res["state"]["X"]["entry"], 120.0)

    def test_short_only_when_allowed(self):
        bars = flat(40)
        bars.append(bar(40, 100, 101, 90, 91))           # 20일 최저가 98 이탈
        self.assertEqual(turtle.run({"X": bars}, False, 0.0, 10_000.0)["state"]["X"], None)
        st = turtle.run({"X": bars}, True, 0.0, 10_000.0, slip_pct=0.0)["state"]["X"]
        self.assertEqual(st["dir"], -1)
        self.assertAlmostEqual(st["entry"], 98.0)
        self.assertAlmostEqual(st["stop"], 98.0 + 8.0)

    def test_ambiguous_day_skipped(self):
        bars = flat(40)
        bars.append(bar(40, 100, 110, 90, 100))          # 위아래 다 뚫음
        res = turtle.run({"X": bars}, True, 0.0, 10_000.0)
        self.assertIsNone(res["state"]["X"])
        self.assertEqual(res["skipped"], 1)

    def test_notional_cap_and_fees(self):
        bars = flat(40, rng=0.05)                        # N = 0.1 → 1% ÷ 0.1 = 계좌의 100배 명목가 → 캡
        bars.append(bar(40, 100, 110, 99.9, 109))
        res = turtle.run({"X": bars}, False, 0.1, 10_000.0, slip_pct=0.0)
        st = res["state"]["X"]
        self.assertTrue(st["capped"])
        self.assertAlmostEqual(st["units"] * st["entry"], 10_000.0)   # 명목가 = 계좌 100%
        # 수수료: 같은 가격에 청산하면 왕복 0.2% 손실
        bars.append(bar(41, 100.05, 100.1, 90, 95))
        res = turtle.run({"X": bars}, False, 0.1, 10_000.0, slip_pct=0.0)
        t = res["trades"][0]
        self.assertAlmostEqual(t["fees"], (t["entry"] + t["exit"]) * t["units"] * 0.001, places=3)

    def test_shared_account(self):
        """두 시장이 한 계좌를 나눠 쓰면 두 번째 진입은 첫 진입 뒤 남은 명목가 안에서만."""
        a = flat(40, rng=0.05)
        a.append(bar(40, 100, 110, 99.9, 109))
        b = flat(40, rng=0.05)
        b.append(bar(40, 100, 110, 99.9, 109))
        res = turtle.run({"A": a, "B": b}, False, 0.0, 10_000.0, slip_pct=0.0)
        used = sum(p["units"] * p["entry"] for p in res["state"].values() if p)
        self.assertAlmostEqual(used, 10_000.0, places=4)

    def test_stats_and_signal(self):
        bars = self._trend()
        res = turtle.run({"X": bars}, False, 0.05, 10_000.0)
        st = turtle.stats(res, 10_000.0, {"X": bars})
        self.assertEqual(st["trades"], 1)
        self.assertEqual(st["wins"], 1)
        self.assertAlmostEqual(st["realized_net_pct"], (res["equity"] / 10_000 - 1) * 100, places=3)
        self.assertLessEqual(st["max_drawdown_pct"], 0.0)
        self.assertIn("buy_hold_pct", st)
        self.assertEqual(st["forward"]["trades"], 1 if "2026-01-01" >= turtle.RULE_FIXED else 0)
        sg = turtle.signal(bars, res["state"]["X"], False, 10_000.0)
        self.assertEqual(sg["position"], "flat")
        self.assertAlmostEqual(sg["entry_long_at"], max(x["h"] for x in bars[-20:]))
        self.assertAlmostEqual(sg["stop_if_long"], sg["entry_long_at"] - 2 * sg["n"], places=3)
        sg2 = turtle.signal(bars[:42], {"dir": 1, "entry": 102.0, "stop": 94.0, "units": 1.0, "entry_date": "x"}, False, 10_000.0)
        self.assertEqual(sg2["position"], "long")
        self.assertAlmostEqual(sg2["exit_level"], max(94.0, min(x["l"] for x in bars[32:42])))


if __name__ == "__main__":
    unittest.main()
