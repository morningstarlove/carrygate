# -*- coding: utf-8 -*-
"""
scalp.py 계산 로직 검증 — 외부 접속 없이 가짜 데이터로 돌린다.

  python3 -m unittest tests.test_scalp -v
"""
import json, math, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scalp  # noqa: E402


def mk(prices, vols=None):
    """종가 목록 -> 봉 목록. 시가는 직전 종가, 고저는 둘의 최대/최소."""
    bars, prev = [], prices[0]
    for i, c in enumerate(prices):
        v = vols[i] if vols else 1.0
        bars.append(scalp.bar(i * 60, prev, max(prev, c), min(prev, c), c, v))
        prev = c
    return bars


FEE = {"taker_pct": 0.05, "maker_pct": 0.02, "short_ok": True, "fee_source": "test", "kind": "perp"}


class FillTiming(unittest.TestCase):
    def test_entry_uses_next_bar_open(self):
        # 300봉 평탄 -> 한 번 급락(z<-2) -> 회복. 진입은 신호 다음 봉 시가여야 한다.
        px = [100.0] * 300 + [90.0] + [100.0] * 50
        # 평탄 구간은 표준편차 0 이라 z 가 없다. 미세한 흔들림을 준다.
        px = [p + (0.01 if i % 2 else -0.01) for i, p in enumerate(px)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        tr = scalp.backtest(ctx, st, True)
        self.assertTrue(tr, "급락 뒤 평균회귀 매수가 있어야 한다")
        t = tr[0]
        self.assertEqual(t["side"], 1)
        # 신호 봉(300)의 종가 90 -> 다음 봉(301)의 시가 = 직전 종가 90 에 체결
        self.assertEqual(t["entry_i"], 301)
        self.assertAlmostEqual(ctx.o[301], px[300])
        self.assertGreater(t["gross_pct"], 5.0)   # 90 -> 100 근처 회복

    def test_no_entries_before_eval_window(self):
        px = [100.0 + math.sin(i / 3.0) * 3 for i in range(600)]
        ctx = scalp.Ctx(mk(px), eval_start=400)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        for t in scalp.backtest(ctx, st, True):
            self.assertGreaterEqual(t["entry_i"], 400)

    def test_spot_never_shorts(self):
        px = [100.0 + math.sin(i / 3.0) * 3 for i in range(600)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        for st in scalp.STRATEGIES:
            for t in scalp.backtest(ctx, st, short_ok=False):
                self.assertEqual(t["side"], 1, st["key"])

    def test_max_hold_respected(self):
        px = [100.0 + math.sin(i / 7.0) * 2 for i in range(900)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        for st in scalp.STRATEGIES:
            for t in scalp.backtest(ctx, st, True):
                self.assertLessEqual(t["hold"], st["max_hold"], st["key"])


class Arithmetic(unittest.TestCase):
    def test_net_equals_gross_minus_fees(self):
        trades = [{"side": 1, "entry_i": 10, "exit_i": 15, "hold": 5, "gross_pct": 0.3, "forced": False},
                  {"side": -1, "entry_i": 20, "exit_i": 22, "hold": 2, "gross_pct": -0.1, "forced": False},
                  {"side": 1, "entry_i": 40, "exit_i": 41, "hold": 1, "gross_pct": 0.05, "forced": True}]
        s = scalp.summarize(trades, rt_taker=0.11, rt_maker=0.04, mid_i=30)
        self.assertEqual(s["trades"], 3)
        self.assertAlmostEqual(s["gross_pct"], 0.25)
        self.assertAlmostEqual(s["fee_taker_pct"], 0.33)
        self.assertAlmostEqual(s["net_taker_pct"], 0.25 - 0.33)
        self.assertAlmostEqual(s["net_maker_pct"], 0.25 - 0.12)
        self.assertAlmostEqual(s["first_half_net_pct"] + s["second_half_net_pct"], s["net_taker_pct"])
        self.assertEqual(s["forced_close"], 1)
        self.assertEqual(s["longs"], 2)
        self.assertEqual(s["shorts"], 1)
        self.assertEqual(s["win_rate_pct"], round(100.0 / 3, 1))   # 0.3-0.11 만 플러스

    def test_empty_trades(self):
        s = scalp.summarize([], 0.1, 0.04, 0)
        self.assertEqual(s["trades"], 0)
        self.assertEqual(s["net_taker_pct"], 0.0)
        self.assertIsNone(s["win_rate_pct"])

    def test_hurdle_needed_win_rate(self):
        # 1분마다 정확히 ±0.05% 움직이는 시장, 왕복 비용 0.1% -> 필요 승률 (1+0.1/0.05)/2 = 150%
        px, p = [], 100.0
        for i in range(500):
            p = p * (1.0005 if i % 2 else 1 / 1.0005)
            px.append(p)
        ctx = scalp.Ctx(mk(px), eval_start=100)
        h = scalp.hurdle(ctx, 0.1)
        self.assertAlmostEqual(h["h1"]["mean_abs_move_pct"], 0.05, places=3)
        self.assertAlmostEqual(h["h1"]["needed_win_rate_pct"], 150.0, places=0)
        self.assertEqual(h["h1"]["share_above_cost_pct"], 0.0)

    def test_tick_estimate(self):
        bars = mk([133.0, 134.0, 133.0, 135.0, 134.0])
        self.assertEqual(scalp.tick_estimate(bars), 1.0)
        bars = mk([113397000.0, 113398000.0, 113395000.0])
        self.assertEqual(scalp.tick_estimate(bars), 1000.0)

    def test_max_drawdown(self):
        self.assertAlmostEqual(scalp.max_drawdown([1, -2, 1, -3, 5]), -4.0)
        self.assertAlmostEqual(scalp.max_drawdown([1, 1, 1]), 0.0)


class Behaviour(unittest.TestCase):
    def test_mean_reversion_wins_on_spike_and_recover_market(self):
        # 평탄한 시장에 40봉마다 급락이 오고 10봉에 걸쳐 회복한다 (급락 = z<-2, 회복 = 이익)
        px = []
        for i in range(1200):
            k = i % 40
            dip = -3.0 * (1 - k / 10.0) if k < 10 else 0.0
            px.append(100.0 + dip + (0.03 if i % 2 else -0.03))
        ctx = scalp.Ctx(mk(px), eval_start=240)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        tr = scalp.backtest(ctx, st, True)
        self.assertGreater(len(tr), 10)
        self.assertGreater(sum(t["gross_pct"] for t in tr), 0)

    def test_breakout_wins_on_trending_market(self):
        px = [100.0 * (1.0005 ** i) + (0.02 if i % 2 else -0.02) for i in range(600)]
        ctx = scalp.Ctx(mk(px), eval_start=240)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "brk_20")
        tr = scalp.backtest(ctx, st, True)
        self.assertTrue(tr)
        self.assertGreater(sum(t["gross_pct"] for t in tr), 0)
        self.assertTrue(all(t["side"] == 1 for t in tr))

    def test_evaluate_marks_costs_and_robustness(self):
        px = [100.0 + math.sin(i / 4.0) * 2 for i in range(1200)]
        rows, cost, hz = scalp.evaluate("okx", "BTC", mk(px), 240, FEE)
        self.assertEqual(len(rows), len(scalp.STRATEGIES))
        self.assertAlmostEqual(cost["round_trip_maker_pct"], 0.04)
        self.assertGreaterEqual(cost["round_trip_taker_pct"], 0.10)
        for r in rows:
            self.assertAlmostEqual(r["net_taker_pct"], round(r["gross_pct"] - r["trades"] * cost["round_trip_taker_pct"], 4), places=3)
            if r["robust"]:
                self.assertGreater(r["net_taker_pct"], 0)
                self.assertGreaterEqual(r["trades"], 10)
                self.assertTrue(r["beats_baseline"])


class HistoryFile(unittest.TestCase):
    def test_same_date_overwrites(self):
        out = {"date": "2026-09-29", "eval_day_utc": "2026-09-28", "summary": {"rows_total": 1},
               "rows": [{"venue": "okx", "coin": "BTC", "strategy": "mr_z20", "trades": 3,
                         "gross_pct": 0.1, "net_taker_pct": -0.2, "net_maker_pct": 0.0, "robust": False}],
               "markets": {"okx:BTC": {"hurdle": {"h5": {"needed_win_rate_pct": 120.0}, "h1": {"needed_win_rate_pct": 150.0}},
                                       "cost": {"round_trip_taker_pct": 0.11}}}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            scalp.write_history(out, p)
            scalp.write_history(out, p)
            out2 = dict(out, date="2026-09-30")
            scalp.write_history(out2, p)
            rows = scalp.load_history(p)
            self.assertEqual([r["date"] for r in rows], ["2026-09-29", "2026-09-30"])
            self.assertEqual(rows[0]["rows"]["okx:BTC:mr_z20"]["nt"], -0.2)
            self.assertEqual(rows[0]["hurdle"]["okx:BTC"]["h5_needed_win_rate_pct"], 120.0)


if __name__ == "__main__":
    unittest.main()
