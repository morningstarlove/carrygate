# -*- coding: utf-8 -*-
"""
forecast.py 계산 로직 검증 — 가짜 2시간봉, 인터넷 없음.

  python3 -m unittest tests.test_forecast -v
"""
import os, sys, json, math, random, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import forecast as F  # noqa: E402
import verdict as V   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CRIT = json.load(open(os.path.join(os.path.dirname(HERE), "criteria.json"), encoding="utf-8"))
T0 = 1_700_000_000
STEP = F.BAR_MIN * 60


def walk(n, seed=1, vol=0.01, regime=None):
    """로그 랜덤워크 종가 n 개. regime(i) 가 있으면 그 배수로 변동성을 바꾼다(변동성 뭉침 흉내)."""
    rng = random.Random(seed)
    c = [100.0]
    for i in range(1, n):
        v = vol * (regime(i) if regime else 1.0)
        c.append(c[-1] * math.exp(rng.gauss(0, v)))
    return c


def bars_from(closes, t0=T0):
    return [F._mk(t0 + i * STEP, c, c * 1.001, c * 0.999, c, 100.0) for i, c in enumerate(closes)]


class Helpers(unittest.TestCase):
    def test_percentile_matches_linear_interpolation(self):
        self.assertEqual(F.percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(F.percentile([5], 33.3), 5)
        self.assertAlmostEqual(F.percentile([0, 10], 33.3), 3.33, places=6)
        self.assertIsNone(F.percentile([], 50))

    def test_bucket(self):
        self.assertEqual(F.bucket(0.0, 1.0, 2.0), "low")
        self.assertEqual(F.bucket(1.5, 1.0, 2.0), "mid")
        self.assertEqual(F.bucket(2.0, 1.0, 2.0), "high")
        self.assertIsNone(F.bucket(None, 1.0, 2.0))

    def test_rolling(self):
        r = [None, 0.1, -0.1, 0.2]
        self.assertAlmostEqual(F.roll_sum(r, 3, 2), 0.1)
        self.assertIsNone(F.roll_sum(r, 1, 2))      # r[0] 가 None
        self.assertAlmostEqual(F.roll_std([None, 1.0, 1.0, 1.0], 3, 3), 0.0)


class Predict(unittest.TestCase):
    def test_too_few_bars(self):
        p = F.predict_one([100.0] * 50, [100.0] * 50)
        self.assertFalse(p["ok"])

    def test_shape_and_ranges(self):
        c = walk(F.MIN_BARS + 30); b = walk(F.MIN_BARS + 30, seed=2)
        p = F.predict_one(c, b)
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["hist_points"], F.LOOKBACK)
        self.assertIn(p["size"]["bucket"], F.BUCKETS)
        self.assertGreater(p["size"]["expected_abs_pct"], 0)
        for nm in F.PREDICTORS:
            self.assertTrue(0.0 <= p["direction"][nm]["p_up"] <= 1.0)
        self.assertEqual(p["direction"]["D3"]["n"], F.LOOKBACK)
        self.assertLessEqual(p["direction"]["D2"]["q25_pct"], p["direction"]["D2"]["median_pct"])
        self.assertLessEqual(p["direction"]["D2"]["median_pct"], p["direction"]["D2"]["q75_pct"])

    def test_no_future_leak(self):
        """예측 시점 뒤의 봉을 지워도 예측이 똑같아야 한다."""
        c = walk(F.MIN_BARS + 60); b = walk(F.MIN_BARS + 60, seed=3)
        k = len(c) - 25
        self.assertEqual(F.predict_one(c, b, k=k), F.predict_one(c[:k + 1], b[:k + 1]))

    def test_size_bucket_tracks_volatility_regime(self):
        """변동성이 3배로 뛴 구간 끝에서는 rv12 가 상위 분위, 조용한 구간 끝에서는 하위 분위."""
        n = F.MIN_BARS + 40
        hot = walk(n, seed=4, regime=lambda i: 3.0 if i >= n - 12 else 1.0)
        cold = walk(n, seed=4, regime=lambda i: 0.3 if i >= n - 12 else 1.0)
        b = walk(n, seed=5)
        self.assertEqual(F.predict_one(hot, b)["size"]["bucket"], "high")
        self.assertEqual(F.predict_one(cold, b)["size"]["bucket"], "low")

    def test_d2_fallback_when_analog_too_few(self):
        """상황 닮은 과거가 MIN_ANALOG 미만이면 D2 = D3 이고 fallback 표시."""
        c = walk(F.MIN_BARS + 30, seed=6); b = walk(F.MIN_BARS + 30, seed=7)
        old = F.MIN_ANALOG
        try:
            F.MIN_ANALOG = 10 ** 6
            p = F.predict_one(c, b)
        finally:
            F.MIN_ANALOG = old
        self.assertTrue(p["direction"]["D2"]["fallback"])
        self.assertEqual(p["direction"]["D2"]["p_up"], p["direction"]["D3"]["p_up"])


class Scoring(unittest.TestCase):
    def entry(self, p1, p2, p3, c0=100.0, t_target=T0 + 6 * STEP):
        return {"ok": True, "t_target": t_target, "c0": c0, "size": {"bucket": "high"},
                "direction": {"D1": {"p_up": p1}, "D2": {"p_up": p2}, "D3": {"p_up": p3}}}

    def test_score_entry_brier_and_hit(self):
        e = self.entry(0.7, 0.3, 0.5)
        s = F.score_entry(e, {T0 + 6 * STEP: 101.0})
        self.assertTrue(s["up"])
        self.assertAlmostEqual(s["realized_pct"], math.log(1.01) * 100, places=3)
        self.assertTrue(s["predictors"]["D1"]["hit"]); self.assertFalse(s["predictors"]["D2"]["hit"])
        self.assertIsNone(s["predictors"]["D3"]["hit"])         # p = 0.5 는 판단 없음
        self.assertAlmostEqual(s["predictors"]["D1"]["brier"], 0.09)
        self.assertAlmostEqual(s["predictors"]["D2"]["brier"], 0.49)
        self.assertAlmostEqual(s["predictors"]["D3"]["brier"], 0.25)

    def test_score_entry_waits_for_target_bar(self):
        e = self.entry(0.7, 0.3, 0.5)
        self.assertIsNone(F.score_entry(e, {T0: 100.0}))

    def test_score_history_only_unscored(self):
        rows = [{"date": "2026-11-06", "coins": {"BTC": self.entry(0.6, 0.6, 0.5), "ETH": dict(self.entry(0.6, 0.6, 0.5), scored={"x": 1})}}]
        n = F.score_history(rows, {"BTC": {T0 + 6 * STEP: 99.0}, "ETH": {T0 + 6 * STEP: 99.0}})
        self.assertEqual(n, 1)
        self.assertFalse(rows[0]["coins"]["BTC"]["scored"]["up"])
        self.assertEqual(rows[0]["coins"]["ETH"]["scored"], {"x": 1})

    def make_rows(self, days, ratio=1.5, seed=0, start="2026-11-06"):
        """채점된 가짜 기록. 상위 분위 |수익| 이 하위의 ratio 배. 방향 예측은 정보 없음(무작위)."""
        from datetime import datetime, timedelta
        rng = random.Random(seed)
        d0 = datetime.strptime(start, "%Y-%m-%d")
        rows = []
        for d in range(days):
            coins = {}
            for i, coin in enumerate(F.COINS):
                b = F.BUCKETS[i % 3]
                mag = {"low": 1.0, "mid": 1.2, "high": ratio}[b] * (0.8 + 0.4 * rng.random())
                up = rng.random() < 0.5
                p1, p2 = rng.choice([0.4, 0.6]), rng.choice([0.4, 0.6])
                coins[coin] = {"ok": True, "size": {"bucket": b},
                               "direction": {"D1": {"p_up": p1}, "D2": {"p_up": p2}, "D3": {"p_up": 0.48}},
                               "scored": {"abs_pct": mag, "realized_pct": mag if up else -mag, "up": up,
                                          "predictors": {nm: {"p_up": p, "hit": (p > 0.5) == up, "brier": (p - (1 if up else 0)) ** 2}
                                                         for nm, p in (("D1", p1), ("D2", p2), ("D3", 0.48))}}}
            rows.append({"date": (d0 + timedelta(days=d)).strftime("%Y-%m-%d"), "coins": coins})
        return rows

    def test_summary_ratio_and_halves(self):
        s = F.score_summary(self.make_rows(20, ratio=1.5))
        self.assertEqual(s["days"], 20); self.assertEqual(s["points"], 20 * len(F.COINS)); self.assertEqual(s["missing_days"], 0)
        self.assertAlmostEqual(s["size"]["all"]["ratio_high_low"], 1.5, delta=0.15)
        self.assertEqual(s["size"]["first_half"]["n"]["high"] + s["size"]["second_half"]["n"]["high"], s["size"]["all"]["n"]["high"])
        self.assertIsNotNone(s["direction"]["all"]["D1"]["brier"])

    def test_summary_ignores_before_rule_fixed_and_counts_gaps(self):
        rows = self.make_rows(3, start="2026-11-01") + self.make_rows(2, start="2026-11-10")
        s = F.score_summary(rows, rule_fixed="2026-11-06")
        self.assertEqual(s["days"], 2)
        rows2 = self.make_rows(2, start="2026-11-06") + self.make_rows(1, start="2026-11-10")
        self.assertEqual(F.score_summary(rows2)["missing_days"], 2)

    def test_write_history_dedupes_and_sorts(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            F.write_history([{"date": "2026-11-07", "a": 1}, {"date": "2026-11-06"}, {"date": "2026-11-07", "a": 2}], p)
            rows = F.read_history(p)
            self.assertEqual([r["date"] for r in rows], ["2026-11-06", "2026-11-07"])
            self.assertEqual(rows[1]["a"], 2)


class Judge(unittest.TestCase):
    def rows(self, days, ratio):
        return Scoring().make_rows(days, ratio=ratio)

    def test_not_yet_under_min_days(self):
        j = V.judge_forecast(self.rows(10, 1.5), CRIT["forecast"])
        self.assertEqual(j["status"], V.NOT_YET)
        self.assertEqual(j["predictors"]["D1"]["status"], V.NOT_YET)

    def test_pass_on_stable_size_ratio(self):
        j = V.judge_forecast(self.rows(60, 1.5), CRIT["forecast"])
        self.assertEqual(j["status"], V.PASS, j["reason"])
        # 방향은 무작위 → 잡음·Brier 못 넘어 FAIL (기록 중단 대상)
        self.assertEqual(j["predictors"]["D1"]["status"], V.FAIL)
        self.assertIn("hit_rate_p95", j["noise_floor"])

    def test_fail_when_ratio_collapses(self):
        j = V.judge_forecast(self.rows(60, 1.0), CRIT["forecast"])
        self.assertEqual(j["status"], V.FAIL)

    def test_noise_floor_deterministic(self):
        a = V.judge_forecast(self.rows(60, 1.5), CRIT["forecast"])["noise_floor"]
        b = V.judge_forecast(self.rows(60, 1.5), CRIT["forecast"])["noise_floor"]
        self.assertEqual(a, b)

    def test_build_includes_forecast_track(self):
        out = V.build(files={"forecast_history": self.rows(5, 1.5)})
        self.assertIn("forecast", out["tracks"])
        self.assertIn("forecast", V.history_line(out))


class Fixture(unittest.TestCase):
    def test_fixture_dry_run(self):
        path = os.path.join(HERE, "fixtures", "forecast")
        if not os.path.exists(F.fixture_path(path, "BTC")):
            self.skipTest("fixture 없음")
        out, rows = F.build(fixture=path, history_rows=[])
        self.assertEqual(out["summary"]["coins_ok"], len(F.COINS), out["errors"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], out["date"])
        # 다음 날 채점: 목표 봉을 포함한 봉으로 다시 돌리면 채점이 붙는다
        bars = {c: F.load_fixture(path, c) for c in F.COINS}
        closes = {c: {b["t"]: b["c"] for b in bs} for c, bs in bars.items()}
        for c in F.COINS:
            rows[0]["coins"][c]["t_target"] = bars[c][-1]["t"]        # 마지막 봉을 목표로 가정
        self.assertEqual(F.score_history(rows, closes), len(F.COINS))


if __name__ == "__main__":
    unittest.main()
