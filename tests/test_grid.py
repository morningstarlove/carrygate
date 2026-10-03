# -*- coding: utf-8 -*-
"""
grid.py (종사종팔 v4/v5) 계산 로직 검증 — 외부 접속 없이 손으로 만든 가격열로 돌린다.

  python3 -m unittest tests.test_grid -v
"""
import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grid  # noqa: E402


def bars_from(closes, start_day=1):
    """2024-01-01 부터 하루씩(주말 무시) 종가 목록 -> [(date, close)]"""
    out = []
    import datetime
    d = datetime.date(2024, 1, 1)
    for c in closes:
        out.append((d.isoformat(), float(c)))
        d += datetime.timedelta(days=1)
    return out


class TestRules(unittest.TestCase):
    def test_buys_one_tier_per_day_and_sells_at_target(self):
        # 100 에 사고 다음 날 103 (+3% ≥ 2.75%) 이면 그 종가에 판다. 판 날은 사지 않는다.
        bars = bars_from([100, 103, 103, 103])
        sim = grid.simulate(bars, "v5", fee_pct=0.0)
        self.assertEqual(len(sim["trades"]), 1)
        t = sim["trades"][0]
        self.assertEqual((t["buy_date"], t["sell_date"], t["reason"]), ("2024-01-01", "2024-01-02", "target"))
        self.assertAlmostEqual(t["ret_pct"], 3.0, places=9)
        # 1/2 에 팔았으니 1/2 에는 매수 없음, 1/3·1/4 에 각각 1티어 → 보유 2
        self.assertEqual(len(sim["tiers"]), 2)
        self.assertEqual([x["buy_date"] for x in sim["tiers"]], ["2024-01-03", "2024-01-04"])

    def test_forced_sell_after_hold_days(self):
        # 가격이 계속 같으면 익절은 없고, 산 지 10거래일째 종가에 강제 매도된다.
        bars = bars_from([100.0] * 12)
        sim = grid.simulate(bars, "v5", fee_pct=0.0)
        first = [t for t in sim["trades"] if t["buy_date"] == "2024-01-01"]
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["sell_date"], "2024-01-11")
        self.assertEqual(first[0]["hold_days"], 10)
        self.assertEqual(first[0]["reason"], "expire")
        self.assertAlmostEqual(first[0]["pnl"], 0.0, places=9)

    def test_fee_is_charged_both_ways(self):
        # 가격 불변, 수수료 편도 1% → 매수 수량 = 금액/(가격×1.01), 매도 수취 = 수량×가격×0.99 (원문 식)
        bars = bars_from([100.0] * 12)
        sim = grid.simulate(bars, "v5", fee_pct=1.0)
        t = sim["trades"][0]
        self.assertAlmostEqual(t["ret_pct"], (0.99 / 1.01 - 1.0) * 100.0, places=9)

    def test_v5_daily_amount_is_pct_of_yesterday_equity(self):
        bars = bars_from([100, 100, 100])
        sim = grid.simulate(bars, "v5", fee_pct=0.0, capital=10000.0)
        # 첫날 1,000 (10,000 의 10%), 둘째 날도 자산 10,000 그대로 → 1,000
        self.assertAlmostEqual(sim["tiers"][0]["cost"], 1000.0)
        self.assertAlmostEqual(sim["tiers"][1]["cost"], 1000.0)
        bars = bars_from([100, 200])
        sim = grid.simulate(bars, "v5", fee_pct=0.0, capital=10000.0)
        # 둘째 날 종가 200: 첫 티어가 +100% 로 익절(매도일 → 매수 없음). 자산 = 9000 + 2000 = 11000
        self.assertEqual(len(sim["trades"]), 1)
        self.assertEqual(len(sim["tiers"]), 0)
        self.assertAlmostEqual(sim["equity"][-1], 11000.0)

    def test_v4_fixed_amount_and_ratchet(self):
        # v4 는 자산이 줄어도 금액을 유지한다. 첫 사이클 = 1,000 고정.
        bars = bars_from([100, 90, 80, 70])
        sim = grid.simulate(bars, "v4", fee_pct=0.0, capital=10000.0)
        self.assertTrue(all(abs(t["cost"] - 1000.0) < 1e-9 for t in sim["tiers"]))
        # 래칫: 2사이클 전(사이클 0) 에 산 티어들의 손익이 +면 사이클 2 부터 70%/10 만큼 금액이 는다
        closes = [100.0] + [103.0] * 30     # 첫날 산 티어만 +3% 익절, 나머지는 103 에서 만기 매도(손익 0)
        bars = bars_from(closes)
        sim = grid.simulate(bars, "v4", fee_pct=0.0, capital=10000.0)
        # 사이클0 손익 = 첫 티어 +30 → 사이클 2 (k≥20) 부터 하루 금액 1000 + 0.7*30/10 = 1002.1
        # (k=12~21 은 날마다 티어 하나가 만기 매도되므로 매수가 없고, k=22 에 매수가 다시 시작된다)
        costs = dict((t["buy_date"], t["cost"]) for t in sim["trades"] + sim["tiers"])
        self.assertAlmostEqual(costs["2024-01-03"], 1000.0)   # 사이클 0 (k=2)
        self.assertAlmostEqual(costs["2024-01-12"], 1000.0)   # 사이클 1 (k=11)
        self.assertNotIn("2024-01-22", costs)                  # k=21: 만기 매도일 → 매수 없음
        self.assertAlmostEqual(costs["2024-01-23"], 1002.1)   # 사이클 2 (k=22)
        # 손실 사이클은 금액을 줄이지 않는다
        bars = bars_from([100.0] + [50.0] * 30)
        sim = grid.simulate(bars, "v4", fee_pct=0.0, capital=10000.0)
        self.assertAlmostEqual(sim["daily_amt"], 1000.0)      # 하루 금액은 그대로
        self.assertTrue(all(t["cost"] <= 1000.0 + 1e-9 for t in sim["trades"] + sim["tiers"]))
        self.assertGreater(sim["cash_short_days"], 0)         # 자산이 줄었는데 금액을 안 줄이니 현금이 모자라는 날이 생긴다

    def test_v4_stops_at_n_tiers(self):
        # v4: 가격이 계속 내려 익절이 없으면 10티어까지만 산다. 11일째는 티어가 가득 차 매수 없음(만기 매도 전).
        bars = bars_from([100.0 - i for i in range(12)])
        sim = grid.simulate(bars, "v4", fee_pct=0.0, capital=100000.0, hold_days=30)
        self.assertEqual(sim["max_tiers"], 10)
        self.assertEqual(len(sim["tiers"]), 10)
        # v5 는 티어 수 제한이 없고 현금이 바닥날 때까지 산다 (매일 자산의 10% → 10일 남짓이면 현금 소진)
        sim5 = grid.simulate(bars, "v5", fee_pct=0.0, capital=100000.0, hold_days=30)
        self.assertEqual(len(sim5["tiers"]), 11)
        self.assertGreaterEqual(sim5["cash_short_days"], 1)

    def test_cash_shortfall_is_recorded(self):
        # v4 로 자산이 반 토막 나도 1,000 씩 사려 들면 현금이 바닥난다 → 남은 만큼만 사고 cash_short_days 가 센다
        # 100 에 1티어, 이후 50 에 9티어(가득) → 만기 매도로 10티어가 5,000 으로 돌아옴 → 다시 1,000 씩 5번 사면 현금 0
        bars = bars_from([100.0] + [50.0] * 30)
        sim = grid.simulate(bars, "v4", fee_pct=0.0, capital=10000.0)
        self.assertGreater(sim["cash_short_days"], 0)
        self.assertGreaterEqual(sim["cash"], -1e-9)


class TestMetrics(unittest.TestCase):
    def test_cagr_and_mdd(self):
        # 자산이 1년(365일) 동안 10,000 → 12,100 이면 CAGR ≈ 21%, 중간에 9,000 까지 빠지면 MDD −10%
        sim = {"dates": ["2024-01-01", "2024-07-01", "2024-12-31"], "equity": [10000.0, 9000.0, 12100.0], "trades": [],
               "max_tiers": 0, "max_buy_ratio": 0.0, "cash_short_days": 0}
        m = grid.metrics(sim)
        self.assertAlmostEqual(m["max_drawdown_pct"], -10.0)
        self.assertAlmostEqual(m["total_return_pct"], 21.0)
        self.assertAlmostEqual(m["cagr_pct"], ((1.21) ** (365.25 / 365.0) - 1.0) * 100.0, places=6)
        self.assertAlmostEqual(m["yearly_ret_pct"]["2024"], 21.0)

    def test_win_rate_and_expire_share(self):
        bars = bars_from([100, 103, 103] + [103.0] * 12)
        sim = grid.simulate(bars, "v5", fee_pct=0.0)
        m = grid.metrics(sim)
        self.assertEqual(m["trades"], len(sim["trades"]))
        wins = sum(1 for t in sim["trades"] if t["pnl"] > 0)
        self.assertAlmostEqual(m["win_rate_pct"], wins / m["trades"] * 100.0)
        self.assertEqual(m["expired_sells"], sum(1 for t in sim["trades"] if t["reason"] == "expire"))


class TestEndToEnd(unittest.TestCase):
    def test_run_all_and_history(self):
        d = tempfile.mkdtemp()
        import datetime
        day = datetime.date(2010, 3, 1)
        with open(os.path.join(d, "SOXL.csv"), "w", encoding="utf-8") as fp:
            fp.write("date,t,o,h,l,c,v\n")
            p = 1.0
            for i in range(6060):   # 2010-03 ~ 2026-10 초
                if day.weekday() < 5:
                    p *= 1.0 + (0.004 if i % 7 in (1, 3, 4, 6) else -0.003)
                    fp.write("%s,0,%s,%s,%s,%s,1\n" % (day.isoformat(), p, p, p, p))
                day += datetime.timedelta(days=1)
        out = grid.run_all(d, ["SOXL", "TQQQ"])
        self.assertEqual(out["missing"], ["TQQQ"])
        res = out["symbols"]["SOXL"]
        self.assertEqual(len(res["windows"]), 2)
        for w in res["windows"].values():
            for v in ("v4", "v5"):
                self.assertIn("cagr_pct", w[v])
                self.assertLessEqual(w[v]["max_drawdown_pct"], 0.0)
        self.assertIn("SOXL", out["comparison"])
        self.assertEqual(len(out["comparison"]["SOXL"]), 4)   # 블로그 주장 4줄 모두 짝지어진다
        self.assertIn("v4", res["forward"])
        hist = os.path.join(d, "h.jsonl")
        grid.write_history(out, hist)
        grid.write_history(out, hist)   # 같은 날 두 번 → 한 줄
        with open(hist, encoding="utf-8") as fp:
            rows = [json.loads(l) for l in fp if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], out["date"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            grid.print_summary(out)
        self.assertIn("블로그 주장 대비", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
