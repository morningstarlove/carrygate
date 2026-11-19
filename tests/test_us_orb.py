# -*- coding: utf-8 -*-
"""
us_orb.py(P40) 계산 로직 검증 — 가짜 5분봉, 인터넷 없음.

  python -m unittest tests.test_us_orb -v
"""
import os, sys, unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import us_orb as U   # noqa: E402

DAY = "2026-11-19"                      # 겨울 시간: 뉴욕 = UTC−5
T0 = int(datetime(2026, 11, 19, 14, 30, tzinfo=timezone.utc).timestamp())   # 09:30 뉴욕


def session(spec, default=100.0, day_t0=T0, last_hm=15 * 60 + 55):
    """spec: {'HH:MM': (o,h,l,c)}. 나머지 봉은 default 로 평평하게 채운다."""
    out = []
    hm = U.OPEN_HM
    while hm <= last_hm:
        o, h, l, c = spec.get(U.hm_str(hm), (default,) * 4)
        out.append({"t": day_t0 + (hm - U.OPEN_HM) * 60, "o": o, "h": h, "l": l, "c": c, "v": 1000.0, "hm": hm})
        hm += U.BAR_MIN
    return out


RANGE = {"09:30": (100, 101, 99.5, 100.5), "09:35": (100.5, 102, 100, 101), "09:40": (101, 101.5, 98, 100)}   # 고가 102, 저가 98, 중간 100


class Clock(unittest.TestCase):
    def test_ny_winter_and_summer(self):
        self.assertEqual(U.ny_parts(T0), (DAY, 570))
        summer = int(datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc).timestamp())
        self.assertEqual(U.ny_parts(summer), ("2026-07-01", 570))

    def test_dst_edges_2026(self):
        self.assertEqual(U.ny_offset_sec(datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc).timestamp()), -5 * 3600)
        self.assertEqual(U.ny_offset_sec(datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc).timestamp()), -4 * 3600)
        self.assertEqual(U.ny_offset_sec(datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc).timestamp()), -4 * 3600)
        self.assertEqual(U.ny_offset_sec(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc).timestamp()), -5 * 3600)

    def test_split_drops_offgrid_and_premarket(self):
        bars = [{"t": T0 - 300, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}, {"t": T0, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
                {"t": T0 + 120, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]
        s = U.split_sessions(bars)
        self.assertEqual([b["hm"] for b in s[DAY]], [570])


class Rounding(unittest.TestCase):
    def test_cents(self):
        self.assertEqual(U.floor_cent(358.326), 358.32)
        self.assertEqual(U.round_cent(370.035), 370.04)


class Rule(unittest.TestCase):
    def test_wick_only_is_not_a_trigger(self):
        spec = dict(RANGE); spec["09:45"] = (101, 103, 101, 102.0)        # 고가만 넘고 종가는 범위 고가와 같음
        self.assertEqual(U.simulate_day(DAY, session(spec))["status"], "no_trigger")

    def test_entry_stop_target(self):
        spec = dict(RANGE); spec["09:45"] = (101, 101.5, 101, 101.0); spec["09:50"] = (101, 103.5, 101, 103.0)   # 09:45 봉은 못 넘음
        spec["10:30"] = (104, 109.5, 104, 109)                              # 목표 109 도달
        r = U.simulate_day(DAY, session(spec, default=103.0))
        self.assertEqual(r["status"], "trade")
        self.assertEqual((r["entry"], r["stop"], r["target"], r["entry_hm"]), (103.0, 100.0, 109.0, "09:55"))
        self.assertEqual((r["reason"], r["exit"], r["r"]), ("target", 109.0, 2.0))
        self.assertAlmostEqual(r["net_pct"], (109 / 103.0 - 1) * 100 - 2 * U.FEE_PCT, places=3)

    def test_stop_first_when_both_in_one_bar(self):
        spec = dict(RANGE); spec["09:50"] = (101, 103.5, 101, 103.0)
        spec["10:00"] = (103, 110, 99.9, 105)
        r = U.simulate_day(DAY, session(spec, default=103.0))
        self.assertEqual((r["reason"], r["exit"], r["r"]), ("stop", 100.0, -1.0))

    def test_gap_through_stop_fills_at_open(self):
        spec = dict(RANGE); spec["09:50"] = (101, 103.5, 101, 103.0)
        spec["10:00"] = (99.0, 99.5, 98.5, 99.2)
        r = U.simulate_day(DAY, session(spec, default=103.0))
        self.assertEqual((r["reason"], r["exit"]), ("stop_gap", 99.0))

    def test_flat_at_1550_bar_close(self):
        spec = dict(RANGE); spec["09:50"] = (101, 103.5, 101, 103.0)
        spec["15:50"] = (103, 104.5, 103, 104.0); spec["15:55"] = (104, 120, 90, 90)   # 15:55 봉은 쓰지 않는다
        r = U.simulate_day(DAY, session(spec, default=103.0))
        self.assertEqual((r["reason"], r["exit"], r["exit_hm"]), ("flat", 104.0, "15:55"))

    def test_no_entry_after_1530(self):
        spec = dict(RANGE); spec["15:25"] = (101, 103.5, 101, 103.0)        # 15:30 에 닫힘 → 허용
        self.assertEqual(U.simulate_day(DAY, session(spec))["status"], "trade")
        spec = dict(RANGE); spec["15:30"] = (101, 103.5, 101, 103.0)        # 15:35 에 닫힘 → 불가
        self.assertEqual(U.simulate_day(DAY, session(spec))["status"], "no_trigger")

    def test_one_trade_per_day(self):
        spec = dict(RANGE); spec["09:50"] = (101, 103.5, 101, 103.0); spec["10:00"] = (103, 103, 99, 99)
        spec["11:00"] = (100, 104, 100, 103.5)                              # 손절 뒤 다시 넘어도 재진입 없음
        r = U.simulate_day(DAY, session(spec, default=103.0))
        self.assertEqual(r["reason"], "stop")

    def test_missing_range_bar_and_incomplete(self):
        s = [b for b in session(dict(RANGE)) if b["hm"] != 575]
        self.assertEqual(U.simulate_day(DAY, s)["status"], "no_range")
        s = session(dict(RANGE), last_hm=12 * 60)
        self.assertEqual(U.simulate_day(DAY, s)["status"], "incomplete")

    def test_early_close_day(self):
        day = "2026-11-27"
        t0 = int(datetime(2026, 11, 27, 14, 30, tzinfo=timezone.utc).timestamp())
        spec = dict(RANGE); spec["12:30"] = (101, 103.5, 101, 103.0)        # 12:35 에 닫힘 → 조기 폐장일엔 불가
        self.assertEqual(U.simulate_day(day, session(spec, day_t0=t0, last_hm=12 * 60 + 55))["status"], "no_trigger")
        spec = dict(RANGE); spec["09:50"] = (101, 103.5, 101, 103.0); spec["12:50"] = (103, 104, 103, 103.5)
        r = U.simulate_day(day, session(spec, default=103.0, day_t0=t0, last_hm=12 * 60 + 55))
        self.assertEqual((r["reason"], r["exit"], r["exit_hm"]), ("flat", 103.5, "12:55"))

    def test_risk_too_small(self):
        rng = {"09:30": (100, 100.02, 100.0, 100.01), "09:35": (100.01, 100.02, 100.0, 100.01), "09:40": (100.01, 100.02, 100.0, 100.01)}
        rng["09:45"] = (100.01, 100.03, 100.01, 100.03)
        self.assertEqual(U.simulate_day(DAY, session(rng, default=100.01))["status"], "risk_small")


class Aggregate(unittest.TestCase):
    def rows(self, nets_by_day):
        out = []
        for i, nets in enumerate(nets_by_day):
            out.append({"date": "2026-11-%02d" % (19 + i), "n": len(nets), "sum_net_pct": sum(nets),
                        "trades": [{"net_pct": x, "reason": "flat"} for x in nets]})
        return out

    def test_day_record_and_aggregate(self):
        spec = dict(RANGE); spec["09:50"] = (101, 103.5, 101, 103.0)
        per = {"AAA": U.simulate_day(DAY, session(spec, default=103.0)), "BBB": U.simulate_day(DAY, session(dict(RANGE)))}
        rec = U.day_record(DAY, per)
        self.assertEqual((rec["n"], rec["no_trigger"], rec["forward"], rec["trades"][0]["sym"]), (1, 1, True, "AAA"))
        a = U.aggregate([rec])
        self.assertEqual((a["sessions"], a["trades"]), (1, 1))
        self.assertAlmostEqual(a["avg_net_pct"], -2 * U.FEE_PCT, places=4)

    def test_judge(self):
        self.assertTrue(U.judge(U.aggregate(self.rows([[1.0]] * 5))).startswith("기록 중"))
        self.assertTrue(U.judge(U.aggregate(self.rows([[-0.5, 0.2]] * 20))).startswith("탈락"))
        self.assertTrue(U.judge(U.aggregate(self.rows([[0.5, 0.3]] * 20))).startswith("후보"))
        self.assertTrue(U.judge(U.aggregate(self.rows([[5.0, -4.9]] * 20))).startswith("보류"))

    def test_compute_sessions_skips_today_when_asked(self):
        spec = dict(RANGE); spec["09:50"] = (101, 103.5, 101, 103.0)
        bars = [{k: v for k, v in b.items() if k != "hm"} for b in session(spec, default=103.0)]
        self.assertEqual(len(U.compute_sessions({"AAA": bars})), 1)
        self.assertEqual(U.compute_sessions({"AAA": bars}, complete_before=DAY), [])

    def test_rules_locked(self):
        import json, verdict as V
        lock = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules_lock.json"), encoding="utf-8"))
        self.assertEqual(lock["us_orb"]["rules"], json.loads(json.dumps(U.rules_dict())))
        self.assertEqual(lock["us_orb"]["rule_fixed"], U.RULE_FIXED)


if __name__ == "__main__":
    unittest.main()
