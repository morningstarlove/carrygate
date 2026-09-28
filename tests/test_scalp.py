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
# 참고: 시장가 체결 1회 비용 = taker + 호가폭/2, 지정가 = maker


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
        mk_t = lambda side, ei, xi, g, forced=False, em=False, xm=False: {
            "side": side, "entry_i": ei, "exit_i": xi, "hold": xi - ei, "gross_pct": g,
            "forced": forced, "entry_maker": em, "exit_maker": xm}
        trades = [mk_t(1, 10, 15, 0.3), mk_t(-1, 20, 22, -0.1), mk_t(1, 40, 41, 0.05, forced=True)]
        # 시장가 체결 1회 = 0.05 + 0.01/2 = 0.055 -> 왕복 0.11
        s = scalp.summarize(trades, FEE, spread_pct=0.01, mid_i=30)
        self.assertEqual(s["trades"], 3)
        self.assertAlmostEqual(s["gross_pct"], 0.25)
        self.assertAlmostEqual(s["fee_pct"], 0.33)
        self.assertAlmostEqual(s["net_pct"], 0.25 - 0.33)
        self.assertAlmostEqual(s["net_if_all_maker_pct"], 0.25 - 0.12)
        self.assertEqual(s["maker_sides"], 0)
        self.assertEqual(s["taker_sides"], 6)
        self.assertAlmostEqual(s["first_half_net_pct"] + s["second_half_net_pct"], s["net_pct"])
        self.assertEqual(s["forced_close"], 1)
        self.assertEqual(s["longs"], 2)
        self.assertEqual(s["shorts"], 1)
        self.assertEqual(s["win_rate_pct"], round(100.0 / 3, 1))   # 0.3-0.11 만 플러스

    def test_mixed_maker_taker_fees(self):
        trades = [{"side": 1, "entry_i": 10, "exit_i": 15, "hold": 5, "gross_pct": 0.3, "forced": False,
                   "entry_maker": True, "exit_maker": False}]
        s = scalp.summarize(trades, FEE, spread_pct=0.01, mid_i=30)
        # 진입 지정가 0.02 + 청산 시장가 (0.05 + 0.005) = 0.075
        self.assertAlmostEqual(s["fee_pct"], 0.075)
        self.assertAlmostEqual(s["net_pct"], 0.3 - 0.075)
        self.assertEqual(s["maker_sides"], 1)
        self.assertEqual(s["taker_sides"], 1)

    def test_empty_trades(self):
        s = scalp.summarize([], FEE, 0.01, 0)
        self.assertEqual(s["trades"], 0)
        self.assertEqual(s["net_pct"], 0.0)
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
        # 1200봉 = 1분봉 1200 / 5분봉 240 / 15분봉 80 -> 세 시간 단위 모두 평가된다
        self.assertEqual(len(rows), len(scalp.STRATEGIES) * len(scalp.TIMEFRAMES))
        self.assertEqual(sorted(set(r["tf"] for r in rows)), ["15m", "1m", "5m"])
        self.assertAlmostEqual(cost["round_trip_maker_pct"], 0.04)
        self.assertGreaterEqual(cost["round_trip_taker_pct"], 0.10)
        self.assertIn("h5", hz)
        for r in rows:
            per_taker = FEE["taker_pct"] + cost["spread_pct_est"] / 2.0
            fee = r["maker_sides"] * FEE["maker_pct"] + r["taker_sides"] * per_taker
            self.assertAlmostEqual(r["fee_pct"], fee, places=3, msg=r["strategy"])
            self.assertAlmostEqual(r["net_pct"], r["gross_pct"] - r["fee_pct"], places=3)
            if r["exec"] == "market":
                self.assertEqual(r["maker_sides"], 0)
            if r["robust"]:
                self.assertGreater(r["net_pct"], 0)
                self.assertGreaterEqual(r["trades"], 10)
                self.assertTrue(r["beats_baseline"])


class Resample(unittest.TestCase):
    def test_five_minute_bars(self):
        bars = [scalp.bar(i * 60, 100 + i, 101 + i, 99 + i, 100.5 + i, 1.0) for i in range(10)]
        tb = scalp.resample(bars, 5)
        self.assertEqual(len(tb), 2)
        self.assertEqual(tb[0]["t"], 0)
        self.assertEqual(tb[0]["o"], 100)          # 첫 봉 시가
        self.assertEqual(tb[0]["c"], 104.5)        # 마지막 봉 종가
        self.assertEqual(tb[0]["h"], 105)          # 최고
        self.assertEqual(tb[0]["l"], 99)           # 최저
        self.assertEqual(tb[0]["v"], 5.0)          # 합계
        self.assertEqual(tb[1]["t"], 300)
        self.assertIs(scalp.resample(bars, 1), bars)

    def test_gap_minutes_do_not_create_empty_bars(self):
        bars = [scalp.bar(0, 1, 1, 1, 1, 1), scalp.bar(60 * 12, 2, 2, 2, 2, 1)]
        tb = scalp.resample(bars, 5)
        self.assertEqual([b["t"] for b in tb], [0, 600])


class LimitOrders(unittest.TestCase):
    def setUp(self):
        # 5봉에 걸쳐 급락하고 5봉에 걸쳐 회복하는 V자 시장(40봉 주기).
        # 급락 도중 신호가 나므로 다음 봉 저가가 주문가 아래로 내려가 지정가가 체결된다.
        px = []
        for i in range(600):
            k = i % 40
            dip = -3.0 * (k + 1) / 5.0 if k < 5 else (-3.0 * (10 - k) / 5.0 if k < 10 else 0.0)
            px.append(100.0 + dip + (0.03 if i % 2 else -0.03))
        self.bars = mk(px)
        self.ctx = scalp.Ctx(self.bars, eval_start=240)
        self.st = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20_lim")

    def test_limit_fill_requires_price_through(self):
        # 매수 지정가: 저가가 주문가보다 낮아야 체결. 같으면 미체결.
        ctx = scalp.Ctx(mk([100.0, 100.0, 100.0]), 0)
        ctx.l[1] = 99.0
        self.assertTrue(scalp.limit_filled(ctx, 1, 1, 99.5))
        self.assertFalse(scalp.limit_filled(ctx, 1, 1, 99.0))
        ctx.h[1] = 101.0
        self.assertTrue(scalp.limit_filled(ctx, 1, -1, 100.5))
        self.assertFalse(scalp.limit_filled(ctx, 1, -1, 101.0))

    def test_limit_entries_are_maker_and_fill_at_limit_price(self):
        tr = scalp.backtest(self.ctx, self.st, True)
        self.assertTrue(tr)
        for t in tr:
            self.assertTrue(t["entry_maker"])
            self.assertLessEqual(t["hold"], self.st["max_hold"])
        # 지정가 진입 가격은 어떤 봉의 종가여야 한다 (신호 봉 종가에 주문)
        closes = set(self.ctx.c)
        for t in tr:
            entry_px = self.ctx.c[t["entry_i"] - 1]
            # 체결 봉의 저가가 주문가보다 낮다 (지나쳤다)
            self.assertLess(self.ctx.l[t["entry_i"]], entry_px + 1e-9)

    def test_limit_variant_pays_less_fee_than_market_variant(self):
        mkt = next(s for s in scalp.STRATEGIES if s["key"] == "mr_z20")
        a = scalp.summarize(scalp.backtest(self.ctx, mkt, True), FEE, 0.01, 400)
        b = scalp.summarize(scalp.backtest(self.ctx, self.st, True), FEE, 0.01, 400)
        self.assertEqual(a["maker_sides"], 0)
        self.assertGreater(b["maker_sides"], 0)
        self.assertLess(b["fee_pct"] / max(b["trades"], 1), a["fee_pct"] / max(a["trades"], 1))

    def test_no_fill_in_flat_market(self):
        # 가격이 전혀 움직이지 않으면 지정가는 절대 체결되지 않는다
        px = [100.0 + (0.01 if i % 2 else -0.01) for i in range(400)]
        ctx = scalp.Ctx(mk(px), 240)
        for t in scalp.backtest(ctx, self.st, True):
            self.assertLess(ctx.l[t["entry_i"]], ctx.c[t["entry_i"] - 1])


class HistoryFile(unittest.TestCase):
    def test_same_date_overwrites(self):
        out = {"date": "2026-09-29", "eval_day_utc": "2026-09-28", "summary": {"rows_total": 1},
               "rows": [{"venue": "okx", "coin": "BTC", "tf": "1m", "strategy": "mr_z20", "trades": 3,
                         "gross_pct": 0.1, "fee_pct": 0.3, "net_pct": -0.2, "robust": False}],
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
            self.assertEqual(rows[0]["rows"]["okx:BTC:1m:mr_z20"]["nt"], -0.2)
            self.assertEqual(rows[0]["hurdle"]["okx:BTC"]["h5_needed_win_rate_pct"], 120.0)
            self.assertEqual(rows[0]["rows"]["okx:BTC:1m:mr_z20"]["f"], 0.3)


if __name__ == "__main__":
    unittest.main()
