# -*- coding: utf-8 -*-
"""
momentum.py 계산 로직 검증 — 외부 접속 없이 가짜 데이터로 돌린다.

  python3 -m unittest tests.test_momentum -v
"""
import io, json, os, sys, tempfile, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import momentum  # noqa: E402


def series(start, values):
    """시작 달 + 월말 종가 목록 -> {ym: close}"""
    i0 = momentum.ym_index(start)
    return dict((momentum.ym_str(i0 + k), v) for k, v in enumerate(values))


def grow(n, monthly_rate, base=100.0):
    return [base * (1.0 + monthly_rate) ** k for k in range(n)]


def write_csv(d, ticker, rows):
    with open(os.path.join(d, ticker + ".csv"), "w", encoding="utf-8") as fp:
        fp.write("date,t,o,h,l,c,v\n")
        for date, c in rows:
            fp.write("%s,0,%s,%s,%s,%s,1\n" % (date, c, c, c, c))


class TestResample(unittest.TestCase):
    def test_last_trading_day_of_month(self):
        rows = [("2020-01-02", 10.0), ("2020-01-30", 11.0), ("2020-01-31", 12.0),
                ("2020-02-03", 13.0), ("2020-02-27", 14.0),   # 2월 마지막 거래일은 27일(28·29일 자료 없음)
                ("2020-03-02", 15.0)]
        ms = momentum.resample_month_end(rows)
        self.assertEqual(ms, [("2020-01", "2020-01-31", 12.0), ("2020-02", "2020-02-27", 14.0),
                              ("2020-03", "2020-03-02", 15.0)])

    def test_resample_ignores_input_order_after_load(self):
        with tempfile.TemporaryDirectory() as d:
            write_csv(d, "SPY", [("2020-02-10", 2.0), ("2020-01-31", 1.0), ("2020-02-28", 3.0)])
            m, dt, rg = momentum.load_all(d, ["SPY", "XXX"])
            self.assertEqual(m["SPY"], {"2020-01": 1.0, "2020-02": 3.0})
            self.assertEqual(dt["SPY"]["2020-02"], "2020-02-28")
            self.assertNotIn("XXX", m)       # 없는 파일은 건너뜀


class TestLookback(unittest.TestCase):
    def test_lookback_value(self):
        mc = series("2020-01", [100.0] * 12 + [150.0])
        self.assertAlmostEqual(momentum.lookback_return(mc, "2021-01", 12), 0.5)
        self.assertIsNone(momentum.lookback_return(mc, "2020-06", 12))

    def test_no_lookahead(self):
        n = 30
        spy = grow(n, 0.0) ; spy[-1] = 1000.0              # 마지막 달에 SPY 폭등
        efa = grow(n, 0.01)
        agg = grow(n, 0.001)
        monthly = {"SPY": series("2020-01", spy), "EFA": series("2020-01", efa), "AGG": series("2020-01", agg)}
        last, prev = momentum.ym_str(momentum.ym_index("2020-01") + n - 1), momentum.ym_str(momentum.ym_index("2020-01") + n - 2)
        cfg = momentum.STRATEGIES["gem_12"]
        # 직전 월말 판단은 폭등을 보지 못해 EFA, 폭등 후 월말 판단은 SPY
        self.assertEqual(momentum.decide(cfg, monthly, prev)[0], "EFA")
        self.assertEqual(momentum.decide(cfg, monthly, last)[0], "SPY")
        window = [momentum.ym_str(momentum.ym_index("2020-01") + k) for k in range(12, n)]
        r = momentum.run_strategy("gem_12", monthly, window, cost_pct=0.0)
        self.assertEqual(r["holdings"][-1], "EFA")          # 마지막 달에 든 것은 직전 월말에 고른 EFA
        self.assertAlmostEqual(r["rets"][-1], efa[-1] / efa[-2] - 1.0)   # SPY 폭등 수익은 못 먹는다
        # 판단 시점 이후 자료를 잘라내도 같은 결정
        cut = dict((t, dict((k, v) for k, v in mc.items() if k <= prev)) for t, mc in monthly.items())
        self.assertEqual(momentum.decide(cfg, cut, prev)[0], "EFA")


class TestGem(unittest.TestCase):
    def setUp(self):
        self.cfg = momentum.STRATEGIES["gem_12"]

    def mk(self, spy_rate, efa_rate, bil_rate=0.002, n=14):
        return {"SPY": series("2020-01", grow(n, spy_rate)), "EFA": series("2020-01", grow(n, efa_rate)),
                "AGG": series("2020-01", grow(n, 0.001)), "BIL": series("2020-01", grow(n, bil_rate))}

    def test_picks_higher_risky_when_beats_cash(self):
        a, det = momentum.decide(self.cfg, self.mk(0.01, 0.02), "2021-02")
        self.assertEqual(a, "EFA")
        a, _ = momentum.decide(self.cfg, self.mk(0.03, 0.02), "2021-02")
        self.assertEqual(a, "SPY")

    def test_defensive_when_both_below_cash(self):
        a, _ = momentum.decide(self.cfg, self.mk(-0.01, -0.02), "2021-02")
        self.assertEqual(a, "AGG")
        a, _ = momentum.decide(self.cfg, self.mk(0.001, 0.0005, bil_rate=0.005), "2021-02")   # 플러스여도 현금 이하
        self.assertEqual(a, "AGG")

    def test_missing_bil_treated_as_zero_cash(self):
        m = self.mk(0.01, 0.02)
        del m["BIL"]
        self.assertEqual(momentum.decide(self.cfg, m, "2021-02")[0], "EFA")
        m = self.mk(-0.01, -0.02)
        del m["BIL"]
        self.assertEqual(momentum.decide(self.cfg, m, "2021-02")[0], "AGG")

    def test_dm4_defensive_tlt_or_cash(self):
        n = 14
        base = {"SPY": grow(n, -0.01), "QQQ": grow(n, -0.01), "EFA": grow(n, -0.01), "GLD": grow(n, -0.01)}
        m = dict((k, series("2020-01", v)) for k, v in base.items())
        m["BIL"] = series("2020-01", grow(n, 0.001))
        m["TLT"] = series("2020-01", grow(n, 0.005))
        cfg = momentum.STRATEGIES["dm_4"]
        self.assertEqual(momentum.decide(cfg, m, "2021-02")[0], "TLT")
        m["TLT"] = series("2020-01", grow(n, -0.005))
        self.assertEqual(momentum.decide(cfg, m, "2021-02")[0], "BIL")


class TestCost(unittest.TestCase):
    def test_cost_once_per_switch(self):
        # SPY 는 계속 오르다 정체, EFA 는 뒤늦게 추월 -> 교체 정확히 1회. 자산 수익률을 단순하게 맞춘다.
        n = 18
        spy = [100.0] * 12 + [100.0 * 1.01 ** k for k in range(1, 7)]     # 12~17월: 완만 상승
        efa = [100.0] * 13 + [100.0 * 1.05 ** k for k in range(1, 6)]     # 더 가파르게 상승
        m = {"SPY": series("2020-01", spy), "EFA": series("2020-01", efa), "AGG": series("2020-01", [100.0] * n)}
        window = [momentum.ym_str(momentum.ym_index("2020-01") + k) for k in range(12, n)]
        free = momentum.run_strategy("gem_12", m, window, cost_pct=0.0)
        paid = momentum.run_strategy("gem_12", m, window, cost_pct=1.0)
        sw = sum(1 for i in range(1, len(free["holdings"])) if free["holdings"][i] != free["holdings"][i - 1])
        self.assertGreaterEqual(sw, 1)
        self.assertAlmostEqual(paid["eq"][-1], free["eq"][-1] * (1 - 0.01) ** sw, places=12)
        self.assertEqual(momentum.calc_metrics(window, paid["eq"], paid["rets"], paid["holdings"], paid["defensive"])["switches"], sw)

    def test_no_cost_without_switch_and_first_entry_free(self):
        n = 16
        m = {"SPY": series("2020-01", grow(n, 0.02)), "EFA": series("2020-01", grow(n, 0.01)),
             "AGG": series("2020-01", grow(n, 0.001))}
        window = [momentum.ym_str(momentum.ym_index("2020-01") + k) for k in range(12, n)]
        r = momentum.run_strategy("gem_12", m, window, cost_pct=5.0)
        self.assertEqual(set(r["holdings"]), {"SPY"})
        self.assertAlmostEqual(r["eq"][-1], 1.02 ** (len(window) - 1), places=12)

    def test_cost_two_switches(self):
        # 직접 만든 교체: 월별로 SPY/EFA 순위가 번갈아 바뀌게 12개월 수익률을 조작
        n = 16
        spy = [100.0] * n
        efa = [100.0] * n
        spy[12], efa[12] = 110.0, 105.0     # 12월말: SPY 우세
        spy[13], efa[13] = 100.0, 120.0     # 13월말: EFA 우세 (12개월 전 100 대비)
        spy[14], efa[14] = 130.0, 100.0     # 14월말: SPY 우세
        spy[15], efa[15] = 130.0, 100.0
        m = {"SPY": series("2020-01", spy), "EFA": series("2020-01", efa), "AGG": series("2020-01", [100.0] * n)}
        window = [momentum.ym_str(momentum.ym_index("2020-01") + k) for k in range(12, n)]
        free = momentum.run_strategy("gem_12", m, window, 0.0)
        paid = momentum.run_strategy("gem_12", m, window, 0.5)
        self.assertEqual(free["holdings"], ["SPY", "EFA", "SPY"])
        self.assertAlmostEqual(paid["eq"][-1], free["eq"][-1] * 0.995 ** 2, places=12)


class TestMetrics(unittest.TestCase):
    def test_cagr_ten_percent(self):
        r = 1.1 ** (1 / 12) - 1
        eq = [1.0]
        for _ in range(24):
            eq.append(eq[-1] * (1 + r))
        window = [momentum.ym_str(momentum.ym_index("2020-12") + k) for k in range(25)]
        m = momentum.calc_metrics(window, eq, [r] * 24, ["SPY"] * 24, set())
        self.assertAlmostEqual(m["cagr_pct"], 10.0, places=9)
        self.assertAlmostEqual(m["total_return_pct"], 21.0, places=9)
        self.assertAlmostEqual(m["max_drawdown_pct"], 0.0)
        self.assertAlmostEqual(m["volatility_pct"], 0.0)
        self.assertAlmostEqual(m["worst_12m_pct"], 10.0, places=9)
        self.assertEqual(m["switches"], 0)
        self.assertEqual(m["yearly_pct"].keys(), {"2021", "2022"})
        self.assertAlmostEqual(m["yearly_pct"]["2021"], 10.0, places=9)

    def test_max_drawdown(self):
        self.assertAlmostEqual(momentum.max_drawdown([1.0, 1.2, 0.9, 1.0, 1.1]), 0.9 / 1.2 - 1.0)
        self.assertEqual(momentum.max_drawdown([1.0, 1.1, 1.2]), 0.0)

    def test_defensive_share_and_switches(self):
        eq = [1.0, 1.0, 1.0, 1.0, 1.0]
        window = ["2020-01", "2020-02", "2020-03", "2020-04", "2020-05"]
        m = momentum.calc_metrics(window, eq, [0.0] * 4, ["SPY", "AGG", "AGG", "SPY"], {"AGG"})
        self.assertEqual(m["switches"], 2)
        self.assertAlmostEqual(m["defensive_pct"], 50.0)
        self.assertIsNone(m["worst_12m_pct"])


class TestHistory(unittest.TestCase):
    def out(self, date, asset):
        return {"date": date, "signals": {"gem_12": {"asset": asset, "as_of_month": "2026-09"}},
                "results": {"gem_12": {"metrics": {"cagr_pct": 5.0, "max_drawdown_pct": -20.0, "switches": 3}}}}

    def test_overwrite_by_date(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            momentum.write_history(self.out("2026-10-01", "SPY"), p)
            momentum.write_history(self.out("2026-10-02", "SPY"), p)
            momentum.write_history(self.out("2026-10-01", "AGG"), p)
            rows = momentum.load_history(p)
            self.assertEqual([r["date"] for r in rows], ["2026-10-01", "2026-10-02"])
            self.assertEqual(rows[0]["signals"]["gem_12"]["asset"], "AGG")

    def test_report_last_switch(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            for dt, a in [("2026-08-01", "SPY"), ("2026-09-01", "SPY"), ("2026-10-01", "AGG")]:
                momentum.write_history(self.out(dt, a), p)
            buf = io.StringIO()
            with redirect_stdout(buf):
                momentum.report(p)
            self.assertIn("마지막 교체: 2026-10-01 (SPY -> AGG)", buf.getvalue())


class TestCli(unittest.TestCase):
    def test_no_data_exit_1(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            self.assertEqual(momentum.main(["--dry-run", "--data", d]), 1)

    def test_end_to_end_dry_run_and_save(self):
        with tempfile.TemporaryDirectory() as d:
            n = 40
            for t, rate in [("SPY", 0.01), ("EFA", 0.008), ("AGG", 0.002), ("BIL", 0.001),
                            ("QQQ", 0.012), ("GLD", 0.004), ("TLT", 0.003)]:
                rows = []
                for k, v in enumerate(grow(n, rate)):
                    ym = momentum.ym_str(momentum.ym_index("2018-01") + k)
                    rows.append((ym + "-05", v * 0.99))
                    rows.append((ym + "-27", v))
                write_csv(d, t, rows)
            out_p, hist_p = os.path.join(d, "o.json"), os.path.join(d, "h.jsonl")
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(momentum.main(["--data", d, "--dry-run", "--out", out_p, "--history", hist_p]), 0)
            self.assertFalse(os.path.exists(out_p))
            self.assertIn("gem_12", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(momentum.main(["--data", d, "--out", out_p, "--history", hist_p]), 0)
            with open(out_p, encoding="utf-8") as fp:
                j = json.load(fp)
            self.assertIn("gem_12", j["results"])
            self.assertEqual(j["signals"]["gem_12"]["asset"], "SPY")
            self.assertEqual(len(momentum.load_history(hist_p)), 1)


if __name__ == "__main__":
    unittest.main()
