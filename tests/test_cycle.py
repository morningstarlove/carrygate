# -*- coding: utf-8 -*-
"""
cycle.py(P36)·index_event.py(P37) 계산 로직 검증 — 가짜 일봉, 인터넷 없음.

  python -m unittest tests.test_cycle -v
"""
import os, sys, json, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cycle as C          # noqa: E402
import index_event as IE   # noqa: E402
import verdict as V        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRIT = json.load(open(os.path.join(ROOT, "criteria.json"), encoding="utf-8"))
T_FWD = int(datetime(2026, 11, 13, tzinfo=timezone.utc).timestamp())


def bars(closes, start_day_offset=-250):
    """FORWARD_FROM 기준 start_day_offset 일부터 하루 한 봉."""
    t0 = T_FWD + start_day_offset * C.DAY
    return [{"t": t0 + i * C.DAY, "o": c, "h": c, "l": c, "c": c, "v": 1.0} for i, c in enumerate(closes)]


A = C.ATH_CLOSE


class Helpers(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(C.sma_series([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_closed_only_drops_forming_bar(self):
        b = bars([1, 2, 3], 0)
        self.assertEqual(len(C.closed_only(b, now_ts=b[-1]["t"] + 100)), 2)
        self.assertEqual(len(C.closed_only(b, now_ts=b[-1]["t"] + C.DAY)), 3)

    def test_book_costs(self):
        bk = C.book([{"price": 100.0}, {"price": 50.0}], 1.0, 100.0)
        units = 1 / (100 * C.buy_cost_mult()) + 1 / (50 * C.buy_cost_mult())
        self.assertAlmostEqual(bk["avg_cost"], round(2 / units, 2))
        self.assertAlmostEqual(bk["mtm_pct"], round((units * 100 * C.sell_value_mult() / 2 - 1) * 100, 2))
        self.assertIsNone(C.book([], 1.0, 1.0)["avg_cost"])

    def test_rules_dict_matches_constants(self):
        r = C.rules_dict()
        self.assertEqual(r["ath_close"], 124728.0)
        self.assertEqual([x[1] for x in r["ladder"]], [-0.5, -0.6, -0.7])
        self.assertEqual(r["trend"]["sma_days"], 200)


class Simulate(unittest.TestCase):
    def test_ladder_fills_once_each_and_only_forward(self):
        pre = [A * 0.45] * 250                         # 순방향 전 −55% (사지 않음)
        fwd = [A * 0.55, A * 0.49, A * 0.48, A * 0.39, A * 0.29, A * 0.28]
        sim = C.simulate(bars(pre + fwd))
        names = [(x["name"], x["date"]) for x in sim["fills"] if x["name"].startswith("L")]
        self.assertEqual(names, [("L50", "2026-11-14"), ("L60", "2026-11-16"), ("L70", "2026-11-17")])

    def test_gap_down_fills_several_levels_same_day(self):
        sim = C.simulate(bars([A * 0.6] * 250 + [A * 0.25]))
        self.assertEqual(sorted(x["name"] for x in sim["fills"]), ["L50", "L60", "L70"])

    def test_trend_cross_needs_bear_zone(self):
        base = [A * 0.6] * 250                          # 200일 평균 = A×0.6
        sim = C.simulate(bars(base + [A * 0.58, A * 0.62, A * 0.65]))
        t = [x for x in sim["fills"] if x["name"] == "T200"]
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["date"], "2026-11-14")
        # 낙폭 −30% 위에서 건너면 사지 않는다
        base2 = [A * 0.8] * 250
        sim2 = C.simulate(bars(base2 + [A * 0.79, A * 0.82]))
        self.assertFalse([x for x in sim2["fills"] if x["name"] == "T200"])

    def test_cycle_end_stops_buying(self):
        sim = C.simulate(bars([A * 0.9] * 250 + [A * 1.01, A * 0.3]))
        self.assertEqual(sim["cycle_end"]["date"], "2026-11-13")
        self.assertFalse(sim["fills"])

    def test_dca_every_seven_days(self):
        sim = C.simulate(bars([A * 0.6] * 250 + [A * 0.6] * 15))
        self.assertEqual([x["date"] for x in sim["dca"]], ["2026-11-13", "2026-11-20", "2026-11-27"])

    def test_build_shape_and_pending(self):
        b = bars([A * 0.6] * 252)
        out = C.build(b, "test", now_ts=b[-1]["t"] + C.DAY, past={"cycles": {}})
        self.assertEqual(out["forward_days"], 2)
        self.assertEqual(out["ladder"]["n"], 0)
        self.assertEqual([p["name"] for p in out["pending"]], ["L50", "L60", "L70", "T200"])
        self.assertEqual(out["dca"]["n"], 1)
        row = C.history_row(out)
        self.assertEqual(row["date"], "2026-11-14")


class Judge(unittest.TestCase):
    crit = CRIT["cycle"]

    def mk(self, days, n, lad_avg, dca_avg, mtm, end=None):
        return {"forward_days": days, "bar_date": "x", "close": 1, "dd_pct": -50, "cycle_end": end,
                "ladder": {"n": n, "avg_cost": lad_avg, "mtm_pct": mtm}, "dca": {"n": 5, "avg_cost": dca_avg, "mtm_pct": 1}}

    def test_statuses(self):
        self.assertEqual(V.judge_cycle(self.mk(10, 2, 50, 60, 5), self.crit)["status"], V.NOT_YET)
        self.assertEqual(V.judge_cycle(self.mk(200, 0, None, 60, None), self.crit)["status"], V.NOT_YET)
        self.assertEqual(V.judge_cycle(self.mk(200, 2, 50, 60, 5), self.crit)["status"], V.PASS)
        self.assertEqual(V.judge_cycle(self.mk(200, 2, 50, 60, -5), self.crit)["status"], V.NOT_YET)
        self.assertEqual(V.judge_cycle(self.mk(200, 2, 59, 60, 5), self.crit)["status"], V.NOT_YET)
        self.assertEqual(V.judge_cycle(self.mk(200, 2, 61, 60, 5), self.crit)["status"], V.FAIL)
        self.assertEqual(V.judge_cycle(self.mk(30, 2, 50, 60, 5, end={"date": "d"}), self.crit)["status"], V.PASS)


class IndexEvent(unittest.TestCase):
    ev = dict(IE.EVENTS[0], hold_days=2)

    def test_waiting_before_ref(self):
        r = IE.evaluate(self.ev, {"2026-11-10": 100, "2026-11-12": 90}, {"2026-11-10": 10, "2026-11-12": 9.5})
        self.assertEqual(r["leg_a"]["status"], "기준일 종가 대기")
        self.assertEqual(r["before_fix"]["excess_pct"], round((0.9 - 0.95) * 100, 2))

    def test_legs(self):
        t = {"2026-11-13": 100, "2026-11-20": 95, "2026-11-30": 90, "2026-12-01": 99, "2026-12-02": 108}
        b = {"2026-11-13": 10, "2026-11-20": 10, "2026-11-30": 10, "2026-12-01": 10, "2026-12-02": 10.8}
        r = IE.evaluate(self.ev, t, b)
        self.assertEqual(r["leg_a"]["status"], "끝")
        self.assertEqual(r["leg_a"]["excess_pct"], -10.0)
        self.assertTrue(r["leg_a"]["hypothesis_holds"])
        self.assertEqual(r["leg_b"]["status"], "끝")
        self.assertEqual(r["leg_b"]["excess_pct"], 12.0)
        self.assertEqual(r["leg_b"]["net_pct"], round(12.0 - 4 * IE.FEE_PCT, 2))
        self.assertTrue(r["leg_b"]["hypothesis_holds"])

    def test_leg_a_in_progress(self):
        r = IE.evaluate(self.ev, {"2026-11-13": 100, "2026-11-16": 110}, {"2026-11-13": 10, "2026-11-16": 10})
        self.assertEqual(r["leg_a"]["status"], "진행 중")
        self.assertNotIn("hypothesis_holds", r["leg_a"])


if __name__ == "__main__":
    unittest.main()
