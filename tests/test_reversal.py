# -*- coding: utf-8 -*-
"""
reversal.py 계산 로직 검증 — 가짜 4시간봉, 인터넷 없음.

  python3 -m unittest tests.test_reversal -v
"""
import os, sys, math, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import reversal  # noqa: E402

T0 = 1_700_000_000


class Path:
    """봉을 차례로 쌓는 도우미. 오르는 봉: 고가 = 종가+1, 저가 = 시가−0.5 / 내리는 봉: 고가 = 시가+0.5, 저가 = 종가−1.
    이렇게 하면 V 자 바닥·꼭대기 봉의 저가·고가가 이웃보다 엄격히 낮/높아 피벗이 된다."""

    def __init__(self, start):
        self.bars = []
        self.last = float(start)

    def add(self, o, h, l, c, v=100.0):
        self.bars.append(reversal._mk(T0 + len(self.bars) * 14400, o, h, l, c, v))
        self.last = c
        return self

    def leg(self, to, n, v=100.0):
        frm = self.last
        for k in range(1, n + 1):
            c = frm + (to - frm) * k / n
            o = self.last
            if c >= o:
                self.add(o, c + 1.0, o - 0.5, c, v)
            else:
                self.add(o, o + 0.5, c - 1.0, c, v)
        return self


def setup_path(start=200.0):
    """하락추세(200 → 150 → 170 → 130) → 목선 150(고가 151) → 저점 유지 135 → 반등 148. 바닥 L1 = 봉 25, 유지 저점 L2 = 봉 36."""
    p = Path(start)
    p.leg(150, 10).leg(170, 6).leg(130, 10).leg(150, 6).leg(135, 5).leg(148, 4)
    return p


def breakout(p, vol=300.0):
    return p.add(148, 158, 147, 156, vol)         # 종가 156 > 목선 151, 거래량 3배


def pullback(p):
    return p.add(156, 157, 149, 153)               # 저가 149 ≤ 목선 151 → 151 지정가 체결


class Pivots(unittest.TestCase):
    def test_pivot_low_high(self):
        p = setup_path()
        bars = p.bars
        self.assertTrue(reversal.is_pivot_low(bars, 9))        # 150 바닥
        self.assertTrue(reversal.is_pivot_low(bars, 25))       # 130 바닥
        self.assertTrue(reversal.is_pivot_low(bars, 36))       # 135 유지 저점
        self.assertFalse(reversal.is_pivot_low(bars, 20))
        self.assertTrue(reversal.is_pivot_high(bars, 15))      # 170 고점
        self.assertFalse(reversal.is_pivot_low(bars, len(bars) - 1))   # 오른쪽 봉이 없으면 확정 안 됨

    def test_find_setup(self):
        bars = setup_path().bars
        atr = reversal.wilder_atr(bars)
        su = reversal.find_setup(bars, [9, 25, 36], 36, atr[36])
        self.assertIsNotNone(su)
        self.assertAlmostEqual(su["neckline"], 151.0)
        self.assertAlmostEqual(su["stop"], 134.0)
        self.assertEqual(su["bottom"], 25)
        self.assertEqual(su["prior_low"], 9)
        self.assertGreater(su["lower_high"][0], su["lower_high"][1])

    def test_setup_needs_lower_high(self):
        """앞 구간 고점(161)이 바닥 앞 고점(171)보다 낮으면 하락추세가 아니다 → 구조 없음."""
        bars = setup_path(start=160.0).bars
        atr = reversal.wilder_atr(bars)
        self.assertIsNone(reversal.find_setup(bars, [9, 25, 36], 36, atr[36]))

    def test_setup_needs_held_low(self):
        """유지 저점이 바닥보다 낮으면(저점이 깨짐) 구조 없음."""
        p = Path(200).leg(150, 10).leg(170, 6).leg(130, 10).leg(150, 6).leg(125, 5).leg(140, 4)
        bars = p.bars
        atr = reversal.wilder_atr(bars)
        self.assertIsNone(reversal.find_setup(bars, [9, 25, 36], 36, atr[36]))


class Backtest(unittest.TestCase):
    def _full(self):
        p = setup_path()
        breakout(p)
        pullback(p)
        p.leg(170, 8).leg(160, 4).leg(185, 8).leg(150, 6)        # 상승 → 눌림(피벗 저점 160) → 상승 → 하락(저점 이탈)
        return p.bars

    def test_full_trade_pullback_entry_trail_exit(self):
        bars = self._full()
        res = reversal.run({"X": bars}, 0.0, 10_000.0, slip_pct=0.0)
        self.assertEqual(len(res["trades"]), 1)
        t = res["trades"][0]
        self.assertEqual(t["entry_how"], "pullback_limit")
        self.assertAlmostEqual(t["entry"], 151.0)                         # 목선 지정가
        self.assertAlmostEqual(t["pattern_low"], 134.0)                   # 손절 = 유지 저점
        self.assertAlmostEqual(t["units"], 10_000 * 0.01 / (151.0 - 134.0))
        self.assertEqual(t["reason"], "trail")
        self.assertGreaterEqual(t["stop_raises"], 1)
        self.assertAlmostEqual(t["stop_final"], 159.0)                    # 눌림 저점 160 봉의 저가 = 159
        self.assertAlmostEqual(t["exit"], 159.0)
        self.assertGreater(t["pnl"], 0)
        self.assertAlmostEqual(math.prod(1 + x["ret_pct"] / 100 for x in res["trades"]) * 10_000, res["equity"], places=6)
        self.assertEqual(res["counts"]["setups"], 1)

    def test_breakout_without_volume_is_ignored(self):
        p = setup_path()
        breakout(p, vol=150.0)                                            # 1.5배: 부족
        pullback(p)
        p.leg(170, 8)
        base = reversal.run({"X": p.bars}, 0.0, 10_000.0)
        self.assertEqual(base["trades"], [])
        self.assertIsNone(base["state"]["X"]["pos"])
        self.assertEqual(base["counts"]["fake_breaks"], 1)
        nov = reversal.run({"X": p.bars}, 0.0, 10_000.0, vol_mult=0.0)   # 변형: 거래량 조건 없음
        self.assertIsNotNone(nov["state"]["X"]["pos"])
        self.assertAlmostEqual(nov["state"]["X"]["pos"]["entry"], 151.0)

    def test_no_pullback_no_entry(self):
        p = setup_path()
        breakout(p)
        for k in range(14):                                               # 눌림 없이 계속 위에서 논다
            p.add(156 + k, 158 + k, 155 + k, 157 + k)
        base = reversal.run({"X": p.bars}, 0.0, 10_000.0)
        self.assertEqual(base["trades"], [])
        self.assertIsNone(base["state"]["X"]["pos"])
        self.assertEqual(base["counts"]["no_pullback"], 1)
        be = reversal.run({"X": p.bars}, 0.0, 10_000.0, pullback=False, slip_pct=0.1)
        pos = be["state"]["X"]["pos"]
        self.assertIsNotNone(pos)
        self.assertEqual(pos["how"], "breakout_close")
        self.assertAlmostEqual(pos["entry"], 156.0 * 1.001)

    def test_stop_at_pattern_low(self):
        p = setup_path()
        breakout(p)
        pullback(p)
        p.add(153, 154, 140, 141).add(141, 142, 130, 132)                 # 저가 130 < 134 → 손절
        res = reversal.run({"X": p.bars}, 0.0, 10_000.0, slip_pct=0.1)
        self.assertEqual(len(res["trades"]), 1)
        t = res["trades"][0]
        self.assertEqual(t["reason"], "stop")
        self.assertAlmostEqual(t["exit"], 134.0 * 0.999)
        self.assertAlmostEqual(t["r_multiple"], -1.0, delta=0.25)        # 손절은 계좌 1% 근처 (슬리피지·수수료만큼 더)

    def test_gap_below_stop_fills_at_open(self):
        p = setup_path()
        breakout(p)
        pullback(p)
        p.add(120, 125, 118, 122)                                         # 시가부터 손절 아래
        res = reversal.run({"X": p.bars}, 0.0, 10_000.0, slip_pct=0.0)
        self.assertAlmostEqual(res["trades"][0]["exit"], 120.0)

    def test_low_broken_before_breakout_cancels(self):
        p = setup_path()
        p.add(148, 149, 132, 140)                                         # 돌파 전에 저점 134 이탈
        breakout(p)
        pullback(p)
        res = reversal.run({"X": p.bars}, 0.0, 10_000.0)
        self.assertEqual(res["trades"], [])
        self.assertIsNone(res["state"]["X"]["pos"])
        self.assertEqual(res["counts"]["stopped_before_entry"], 1)

    def test_breakout_before_pivot_confirmation_is_replayed(self):
        """유지 저점이 확정(3봉 뒤)되기 전에 돌파가 나와도 놓치지 않는다."""
        p = Path(200).leg(150, 10).leg(170, 6).leg(130, 10).leg(150, 6).leg(135, 5)
        p.add(135, 160, 134.5, 158, 400)                                  # 저점 다음 봉에 바로 거래량 돌파
        p.add(158, 159, 150, 152).add(152, 153, 151.5, 152.5)             # 눌림(저가 150 ≤ 151) → 151 체결 ... 확정 봉
        res = reversal.run({"X": p.bars}, 0.0, 10_000.0)
        pos = res["state"]["X"]["pos"]
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["entry"], 151.0)
        self.assertEqual(pos["entry_date"], p.bars[-2]["date"])

    def test_shared_account_cap(self):
        bars = self._full()
        res = reversal.run({"A": bars, "B": bars}, 0.0, 10_000.0, slip_pct=0.0)
        for t in res["trades"]:
            self.assertLessEqual(t["units"] * t["entry"], 10_000.0 + 1e-6)
        self.assertEqual(len(res["trades"]), 2)

    def test_stats_and_signal(self):
        bars = self._full()
        res = reversal.run({"X": bars}, 0.05, 10_000.0)
        st = reversal.stats(res, 10_000.0, {"X": bars})
        self.assertEqual(st["trades"], 1)
        self.assertEqual(st["wins"], 1)
        self.assertAlmostEqual(st["realized_net_pct"], (res["equity"] / 10_000 - 1) * 100, places=3)
        self.assertIn("buy_hold_pct", st)
        self.assertEqual(st["forward"]["trades"], 1 if "2023-11-14" >= reversal.RULE_FIXED else 0)
        sg = reversal.signal(bars, res["state"]["X"], 10_000.0)
        self.assertEqual(sg["position"], "flat")
        # 돌파 뒤 눌림 전: 지정가 대기
        p = setup_path()
        breakout(p)
        res2 = reversal.run({"X": p.bars}, 0.0, 10_000.0)
        sg2 = reversal.signal(p.bars, res2["state"]["X"], 10_000.0)
        self.assertEqual(sg2["phase"], "broken")
        self.assertAlmostEqual(sg2["neckline"], 151.0)
        self.assertAlmostEqual(sg2["stop"], 134.0)
        self.assertIn("지정가", sg2["action"])
        # 보유 중
        pullback(p)
        res3 = reversal.run({"X": p.bars}, 0.0, 10_000.0)
        sg3 = reversal.signal(p.bars, res3["state"]["X"], 10_000.0)
        self.assertEqual(sg3["position"], "long")
        self.assertAlmostEqual(sg3["stop"], 134.0)


if __name__ == "__main__":
    unittest.main()
