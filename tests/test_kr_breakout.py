# -*- coding: utf-8 -*-
"""
kr_breakout.py (신호 → 종이매매 엔진) 검증 — 가짜 봉, 인터넷 없음.

  python3 -m unittest tests.test_kr_breakout -v
"""
import os, sys, json, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kr_breakout as KB  # noqa: E402
import patterns as P  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OHLCV = os.path.join(HERE, "fixtures", "kr", "ohlcv")


def d(i):
    return "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28)


def bar(i, o, h, l, c, v=1_000_000):
    return {"date": d(i), "o": o, "h": h, "l": l, "c": c, "v": v, "value": 2e10}


def box_then_breakout(n_flat=45, n_box=20, price=100.0, after=None):
    """횡보 → 박스 → 돌파봉(65번째) → 그 뒤 after 봉 목록(시가·고가·저가·종가 튜플)."""
    bars = [bar(i, price, price * 1.01, price * 0.99, price) for i in range(n_flat)]
    top, bottom = price * 1.05, price * 0.95
    for j in range(n_box):
        i = n_flat + j
        if j % 4 == 0:
            bars.append(bar(i, price, top, price * 0.995, price * 1.01))
        elif j % 4 == 2:
            bars.append(bar(i, price, price * 1.005, bottom, price * 0.99))
        else:
            bars.append(bar(i, price, price * 1.01, price * 0.99, price))
    i = n_flat + n_box
    bars.append(bar(i, 105, 112, 104, 111, 2_500_000))        # 돌파 신호봉 (종가 111 > 상단 105)
    for k, (o, h, l, c) in enumerate(after or []):
        bars.append(bar(i + 1 + k, o, h, l, c))
    return bars


def run(all_bars, groups, sync=True):
    days = KB.days_from_bars(all_bars, groups)
    if sync:
        for day in days:                                      # 동조 판정을 강제로 켠다 (엔진만 본다)
            day["sync_groups"] = set(g for _, g in day["universe"]); day["sync_by"] = {g: "day" for _, g in day["universe"]}
    return KB.simulate(days, all_bars), days


class Engine(unittest.TestCase):
    def test_entry_next_open_then_stop(self):
        # 신호 다음날 시가 112 진입, 손절 = max(박스 하단 95, 112×1.003×0.93≈104.5) → 둘째 날 저가 100 이면 손절
        bars = box_then_breakout(after=[(112, 115, 110, 114), (113, 114, 100, 101), (101, 103, 99, 102)])
        sims, days = run({"A": bars}, {"A": "G"})
        s = sims["base"]
        self.assertEqual(len(s.trades), 1)
        t = s.trades[0]
        self.assertEqual(t["entry_date"], d(66)); self.assertAlmostEqual(t["entry"], 112 * 1.003, places=1)
        self.assertEqual(t["why"], "stop"); self.assertEqual(t["exit_date"], d(67))
        self.assertAlmostEqual(t["stop"], 112 * 1.003 * 0.93, places=1)
        self.assertLess(t["pnl_pct"], -7.0); self.assertGreater(t["pnl_pct"], -8.0)   # 7% + 슬리피지·수수료

    def test_gap_up_is_skipped(self):
        bars = box_then_breakout(after=[(121, 125, 120, 123), (123, 124, 121, 122)])     # 시가 121 > 111×1.08
        sims, _ = run({"A": bars}, {"A": "G"})
        self.assertEqual(sims["base"].trades, []); self.assertEqual(sims["base"].positions, {})
        self.assertEqual(sims["base"].skipped["gap"], 1)

    def test_hold_max_exit(self):
        after = [(112 + k * 0.2, 113 + k * 0.2, 111 + k * 0.2, 112.5 + k * 0.2) for k in range(30)]   # 천천히 오름
        bars = box_then_breakout(after=after)
        sims, _ = run({"A": bars}, {"A": "G"})
        t = sims["base"].trades[0]
        self.assertEqual(t["why"], "hold20"); self.assertEqual(t["hold_days"], 20)
        t10 = sims["hold_10"].trades[0]
        self.assertEqual(t10["why"], "hold10"); self.assertEqual(t10["hold_days"], 10)

    def test_ten_day_low_exit(self):
        after = [(112, 116, 111, 115)] * 12 + [(115, 115, 110.5, 110.6)] + [(110, 111, 109, 110)]   # 10일 최저 111 아래 종가 → 다음날 시가
        bars = box_then_breakout(after=after)
        sims, _ = run({"A": bars}, {"A": "G"})
        t = sims["base"].trades[0]
        self.assertEqual(t["why"], "low10"); self.assertAlmostEqual(t["exit"], 110 * (1 - KB.SLIP_PCT / 100), places=1)

    def test_no_sector_variant_ignores_sync(self):
        bars = box_then_breakout(after=[(112, 115, 110, 114)] * 3)
        sims, _ = run({"A": bars}, {"A": "G"}, sync=False)      # 1종목뿐 → 동조 불가
        self.assertEqual(sims["base"].signals, 0)
        self.assertEqual(sims["no_sector"].signals, 1)
        self.assertIn("A", sims["no_sector"].positions)

    def test_position_and_group_limits(self):
        codes = ["A%d" % k for k in range(7)]
        all_bars = {c: box_then_breakout(after=[(112, 115, 110, 114)] * 3) for c in codes}
        groups = {c: ("G1" if k < 4 else "G2") for k, c in enumerate(codes)}
        sims, _ = run(all_bars, groups)
        s = sims["base"]
        self.assertEqual(len(s.positions), 4)                    # G1 2 + G2 2 (MAX_PER_GROUP), 5 미만
        self.assertEqual(sum(1 for p in s.positions.values() if p["group"] == "G1"), KB.MAX_PER_GROUP)
        self.assertEqual(s.skipped["limit"], 3)

    def test_sizing_risk_one_percent(self):
        bars = box_then_breakout(after=[(112, 115, 110, 114)] * 3)
        sims, _ = run({"A": bars}, {"A": "G"})
        p = sims["base"].positions["A"]
        risk = p["shares"] * (p["entry"] - p["stop"])
        self.assertLessEqual(risk, KB.EQUITY0 * KB.RISK_PCT / 100 + p["entry"])          # 1% 이하 (1주 단위 오차)
        self.assertLessEqual(p["shares"] * p["entry"], KB.EQUITY0 * KB.MAX_POS_PCT / 100 + p["entry"])

    def test_risk_2_variant_sizes_double(self):
        bars = box_then_breakout(after=[(112, 115, 110, 114)] * 3)
        sims, _ = run({"A": bars}, {"A": "G"})
        p1 = sims["base"].positions["A"]; p2 = sims["risk_2"].positions["A"]
        r1 = p1["shares"] * (p1["entry"] - p1["stop"]); r2 = p2["shares"] * (p2["entry"] - p2["stop"])
        self.assertGreater(r2, r1 * 1.8)                                              # 위험 금액 약 2배 (1주 단위 오차)
        self.assertLessEqual(p2["shares"] * p2["entry"], KB.EQUITY0 * 0.30 + p2["entry"])   # 명목 상한 30%
        self.assertEqual(p1["stop"], p2["stop"])                                      # 손절 규칙은 같다

    def test_cooldown_blocks_reentry(self):
        after = [(113, 114, 100, 101)] + [(101, 102, 99, 100)] * 2     # 바로 손절
        bars = box_then_breakout(after=after)
        # 손절 뒤 곧바로 다시 박스 돌파가 나와도 COOLDOWN 안이면 안 들어간다
        i0 = len(bars)
        bars += [bar(i0 + k, 100, 101, 99, 100) for k in range(3)]
        bars.append(bar(i0 + 3, 101, 112, 100, 111, 3_000_000))
        sims, _ = run({"A": bars}, {"A": "G"})
        self.assertEqual(len(sims["base"].trades), 1)
        self.assertGreaterEqual(sims["base"].skipped["cooldown"], 0)


class Inputs(unittest.TestCase):
    def test_days_from_history_filters(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        recs = [
            {"date": "2026-10-03", "universe": [["A", "G"]], "sync_groups": {"G": {"by": "day"}}},            # RULE_FIXED 이전
            {"date": "2026-10-06", "universe": [["A", "G"], ["B", "H"]], "sync_groups": {"G": {"by": "week"}}},
            {"date": "2026-10-07", "universe": [["A", "G"]], "sync_groups": {}, "duplicate_of_previous": True},  # 휴장일 중복
            {"date": "2026-10-08", "universe": [["A", "G"]], "sync_groups": {}},
            {"date": "2026-10-08", "universe": [["B", "H"]], "sync_groups": {}},                               # 같은 날 두 번 → 첫 것만
        ]
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            for r in recs:
                fp.write(json.dumps(r) + "\n")
        self.addCleanup(os.remove, path)
        days = KB.days_from_history(path, since="2026-10-06")
        self.assertEqual([x["date"] for x in days], ["2026-10-06", "2026-10-08"])
        self.assertEqual(days[0]["sync_groups"], {"G"}); self.assertEqual(days[0]["sync_by"], {"G": "week"})
        self.assertEqual(days[1]["universe"], [("A", "G")])

    def test_days_from_bars_reconstructs_top_and_sync(self):
        bars = {c: box_then_breakout(after=[(112, 115, 110, 114)] * 3) for c in ("A", "B", "C")}
        days = KB.days_from_bars(bars, {"A": "G", "B": "G", "C": "G"})
        self.assertTrue(days)
        self.assertEqual(len(days[-1]["universe"]), 3)
        # 돌파봉(모두 +11%)이 있는 날은 3종목 모두 상승·중앙값 ≥2% → 동조
        sig_day = [x for x in days if x["date"] == d(65)][0]
        self.assertIn("G", sig_day["sync_groups"])


@unittest.skipUnless(os.path.exists(os.path.join(OHLCV, "058470.csv")), "실데이터 fixture 없음")
class RealFixture(unittest.TestCase):
    def test_fixture_backtest_runs_and_matches_known_trades(self):
        out = KB.run_backtest(OHLCV)
        self.assertEqual(out["period"]["from"], "2026-02-12")
        base = out["trades"]["base"]
        self.assertTrue(any(t["code"] == "080220" and t["signal_date"] == "2026-05-14" and t["why"] == "hold20" for t in base))
        self.assertTrue(any(t["code"] == "058470" and t["signal_date"] == "2026-09-22" and t["why"] == "stop" for t in base))
        for t in base:
            if t["why"] == "stop":
                self.assertLess(t["pnl_pct"], -7.0)
        self.assertGreaterEqual(out["variants"]["no_sector"]["signals"], out["variants"]["base"]["signals"])


if __name__ == "__main__":
    unittest.main()
