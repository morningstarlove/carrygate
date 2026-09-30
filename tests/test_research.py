# -*- coding: utf-8 -*-
"""
scalp_research.py / orderbook.py 계산 로직 검증 — 가짜 데이터로, 인터넷 없이.

  python3 -m unittest tests.test_research -v
"""
import os, sys, math, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scalp, scalp_research as sr, orderbook as ob  # noqa: E402


def bars_from(closes, t0=0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(scalp.bar(t0 + i * 60, prev, max(prev, c), min(prev, c), c, 1.0))
        prev = c
    return out


class HourlyHurdle(unittest.TestCase):
    def test_buckets_by_utc_hour(self):
        # 0시대는 1분마다 ±0.1%, 1시대는 ±0.5% 로 움직이는 시장 (t0 = UTC 0시)
        px, p = [], 100.0
        for i in range(180):
            step = 0.001 if i < 60 else 0.005
            p = p * (1 + step if i % 2 else 1 - step)
            px.append(p)
        hh = sr.hourly_hurdle(bars_from(px), 0, 0.1, horizons=(1,))
        m = hh["h1_mean_abs_move_pct"]
        # 0시대 마지막 봉의 다음 움직임은 1시대 값이라 평균이 살짝 높다 (59×0.1 + 0.5)/60
        self.assertAlmostEqual(m[0], 0.1, places=1)
        self.assertAlmostEqual(m[1], 0.5, places=1)
        self.assertIsNone(m[5])                      # 자료 없는 시간대
        self.assertGreater(hh["h1_needed_win_rate_pct"][0], hh["h1_needed_win_rate_pct"][1])
        self.assertEqual(hh["h1_n"][0], 60)

    def test_kst_hour(self):
        self.assertEqual(sr.kst_hour(0), 9)
        self.assertEqual(sr.kst_hour(15), 0)


class FundingEvents(unittest.TestCase):
    def test_event_windows_and_sign(self):
        # 정산(t=3600) 직전 15분간 -1%, 직후 15분간 +1% 인 가격. 펀딩 +면 signed 값이 pre<0, post>0
        closes = [100.0] * 121
        for i in range(45, 61):
            closes[i] = 100.0 - (i - 45) / 15.0
        for i in range(61, 76):
            closes[i] = 99.0 + (i - 60) / 15.0
        for i in range(76, 121):
            closes[i] = 100.0
        bars = bars_from(closes)
        ev = sr.funding_event_study(bars, [(3600, 0.0001)], "hyperliquid", "BTC", 0)
        self.assertEqual(len(ev), 1)
        self.assertLess(ev[0]["pre15_bp"], -90)
        self.assertGreater(ev[0]["post15_bp"], 90)
        s = sr.summarize_events(ev)
        self.assertLess(s["signed_pre15_bp"], 0)
        self.assertGreater(s["signed_post15_bp"], 0)
        # 펀딩이 음수면 부호가 뒤집힌다
        ev2 = sr.funding_event_study(bars, [(3600, -0.0001)], "hyperliquid", "BTC", 0)
        self.assertLess(sr.summarize_events(ev2)["signed_post15_bp"], 0)

    def test_event_before_eval_window_ignored(self):
        bars = bars_from([100.0] * 200)
        self.assertEqual(sr.funding_event_study(bars, [(3600, 0.0001)], "okx", "BTC", 7200), [])


class LeadLag(unittest.TestCase):
    def test_leader_predicts_follower(self):
        # 후행 거래소는 선행 거래소의 1분 전 움직임을 그대로 따라간다
        import random
        rnd = random.Random(7)
        lead, follow = [100.0], [100.0]
        steps = [rnd.gauss(0, 0.002) for _ in range(600)]
        for i, s in enumerate(steps):
            lead.append(lead[-1] * (1 + s))
            follow.append(follow[-1] * (1 + (steps[i - 1] if i else 0)))
        r = sr.lead_lag(bars_from(lead), bars_from(follow), 0)
        self.assertGreater(r["n"], 500)
        self.assertGreater(r["corr_lead_next1"], 0.9)
        self.assertLess(abs(r["corr_reverse_next1"]), 0.2)
        self.assertGreater(r["follow_big_next1_bp"], 0)
        self.assertGreater(r["follow_big_hit_rate_pct"], 90)

    def test_too_few_samples(self):
        r = sr.lead_lag(bars_from([100.0] * 10), bars_from([100.0] * 10), 0)
        self.assertNotIn("corr_lead_next1", r)


class VolatilityBreakout(unittest.TestCase):
    def test_enters_on_breakout_and_exits_at_day_end(self):
        # 1일차: 90~110 왕복 (폭 20). 2일차: 시가 100, 정오에 111 로 돌파 후 115 마감
        day1 = [100.0 + 10.0 * math.sin(i / 1440.0 * 2 * math.pi) for i in range(1440)]
        day2 = [100.0] * 720 + [111.0 + (i / 720.0) * 4 for i in range(720)]
        ctx = scalp.Ctx(bars_from(day1 + day2), eval_start=1440)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "vb_05")
        tr = scalp.backtest(ctx, st, True)
        self.assertEqual(len(tr), 1)
        self.assertEqual(tr[0]["side"], 1)
        self.assertGreaterEqual(tr[0]["entry_i"], 1440 + 720)
        self.assertTrue(tr[0]["forced"])           # 자료 끝 = 하루 끝에서 청산
        self.assertGreater(tr[0]["gross_pct"], 2.0)

    def test_no_signal_without_previous_day(self):
        px = [100.0] * 300 + [130.0] * 100
        ctx = scalp.Ctx(bars_from(px), eval_start=240)
        st = next(s for s in scalp.STRATEGIES if s["key"] == "vb_05")
        self.assertEqual(scalp.backtest(ctx, st, True), [])


class OrderbookAnalysis(unittest.TestCase):
    def rows(self, n=200, predictive=True):
        # 10초 간격. 불균형이 +면 60초 뒤 중간가 +0.2%, −면 −0.2% (predictive) / 무관 (아니면)
        out, mid = [], 100.0
        imbs = [0.5 if (i // 12) % 2 == 0 else -0.5 for i in range(n)]
        for i in range(n):
            if predictive and i >= 6:
                mid = mid * (1 + 0.0002 * (1 if imbs[i - 6] > 0 else -1) / 6)
            bq = 100.0 * (1 + imbs[i])
            aq = 100.0 * (1 - imbs[i])
            out.append({"ts": i * 10, "bid": mid * (1 - 0.0002), "ask": mid * (1 + 0.0002), "bq": bq, "aq": aq})
        return out

    def test_imbalance_predicts_when_built_that_way(self):
        m = ob.analyze_market(self.rows(predictive=True))
        self.assertEqual(m["n"], 200)
        self.assertAlmostEqual(m["spread_bp_mean"], 4.0, places=1)
        self.assertGreater(m["h60"]["signed_ret_bp"], 0)
        self.assertGreater(m["h60"]["hit_rate_pct"], 60)

    def test_no_prediction_when_flat(self):
        m = ob.analyze_market(self.rows(predictive=False))
        self.assertEqual(m["h60"]["signed_ret_bp"], 0.0)

    def test_snap_and_notional(self):
        s = ob.snap([(100.0, 1.0), (99.0, 2.0)], [(101.0, 1.0), (102.0, 1.0)])
        self.assertEqual(s["bid"], 100.0)
        self.assertEqual(s["ask"], 101.0)
        self.assertAlmostEqual(s["bid_notional"], 100.0 + 198.0)
        self.assertIsNone(ob.snap([(101.0, 1.0)], [(100.0, 1.0)]))   # 역전된 호가는 버린다

    def test_summary_half_spread_minus_fee(self):
        by = {"okx:BTC": self.rows(), "okx:ETH": self.rows()}
        markets, summary = ob.analyze(by)
        self.assertEqual(summary["okx"]["markets"], 2)
        self.assertAlmostEqual(summary["okx"]["half_spread_minus_maker_fee_bp"], 4.0 / 2 - 2.0, places=1)

    def test_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.csv")
            import csv
            with open(p, "w", newline="") as fp:
                w = csv.DictWriter(fp, fieldnames=ob.CSV_FIELDS)
                w.writeheader()
                w.writerow({"ts": 1, "venue": "okx", "coin": "BTC", "bid": 1, "ask": 2, "bid_notional": 3, "ask_notional": 4})
            by = ob.load_samples(p)
            self.assertEqual(by["okx:BTC"][0]["aq"], 4.0)


class ResearchHistory(unittest.TestCase):
    def test_same_date_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            sr.append_history({"date": "2026-10-01", "x": 1}, p)
            sr.append_history({"date": "2026-10-01", "x": 2}, p)
            rows = sr.load_history(p)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["x"], 2)


if __name__ == "__main__":
    unittest.main()
