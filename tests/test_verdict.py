# -*- coding: utf-8 -*-
"""verdict.py 시험 — 운 보정 z, 사전 등록 판정, 규칙 잠금 대조, 이력 덮어쓰기."""
import os, sys, json, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import verdict as V  # noqa: E402

CRIT = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "criteria.json"), encoding="utf-8"))


def kb_trade(why, pnl_pct):
    return {"why": why, "pnl_pct": pnl_pct, "pnl_krw": int(pnl_pct * 10000)}


def kb_forward(trades, signals=40, no_slot=25):
    wins = sum(1 for t in trades if t["pnl_krw"] > 0)
    n = len(trades)
    base = {"signals": signals, "trades": n, "wins": wins, "win_rate": round(100.0 * wins / n, 2) if n else None,
            "total_return_pct": round(sum(t["pnl_pct"] for t in trades), 2), "max_drawdown_pct": 5.0,
            "skipped": {"limit": no_slot}}
    ctrl = dict(base, trades=n, wins=max(wins - 2, 0), win_rate=20.0, total_return_pct=1.0)
    return {"mode": "forward", "rule_fixed": "2026-10-05", "forward": {"days": 30},
            "variants": {"base": base, "no_sector": ctrl}, "trades": {"base": trades, "no_sector": []}}


BT = {"variants": {"base": {"win_rate": 28.86, "trades": 298}}}


class LuckZ(unittest.TestCase):
    def test_expected_and_z(self):
        r = V.luck_z(100, 20, 0.3)
        self.assertEqual(r["expected_wins"], 30.0)
        self.assertAlmostEqual(r["z"], (20 - 30) / (100 * 0.3 * 0.7) ** 0.5, places=2)

    def test_none_when_no_trades_or_bad_p(self):
        self.assertIsNone(V.luck_z(0, 0, 0.3))
        self.assertIsNone(V.luck_z(10, 3, None))
        self.assertIsNone(V.luck_z(10, 3, 1.0))

    def test_profit_factor(self):
        self.assertEqual(V.profit_factor([10, -5, 20, -5]), 3.0)
        self.assertIsNone(V.profit_factor([1, 2]))


class KrBreakout(unittest.TestCase):
    def test_not_yet_under_min_trades(self):
        trades = [kb_trade("stop", -7.0)] * (CRIT["kr_breakout"]["min_trades"] - 1)
        out = V.judge_kr_breakout(kb_forward(trades), BT, CRIT["kr_breakout"])
        self.assertEqual(out["status"], V.NOT_YET)
        self.assertEqual(out["control"]["variant"], "no_sector")

    def test_luck_warning_before_min_trades(self):
        """운 보정 경고는 표시일 뿐이라 최소 건수 전에도 붙는다 (10/29: 10건 0승 z −2.04 가 안 보였던 틈). 판정은 그대로 NOT_YET."""
        trades = [kb_trade("stop", -7.0)] * 10                       # 0승 10건, 백테스트 28.86% → z ≈ −2.0
        out = V.judge_kr_breakout(kb_forward(trades), BT, CRIT["kr_breakout"])
        self.assertEqual(out["status"], V.NOT_YET)
        self.assertLess(out["luck"]["z"], CRIT["kr_breakout"]["luck_z_warn_below"])
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("운 보정 z", out["warnings"][0])
        ok = [kb_trade("hold20", 5.0)] * 3 + [kb_trade("stop", -7.0)] * 5   # z 가 경고선 위 → 경고 없음
        self.assertEqual(V.judge_kr_breakout(kb_forward(ok), BT, CRIT["kr_breakout"])["warnings"], [])

    def test_pass_when_shape_matches_backtest(self):
        # 승률 30%: 손절 14건(−7%), 20일 보유 6건(전부 승)
        trades = [kb_trade("stop", -7.0)] * 14 + [kb_trade("hold20", 20.0)] * 6
        out = V.judge_kr_breakout(kb_forward(trades), BT, CRIT["kr_breakout"])
        self.assertEqual(out["status"], V.PASS)
        self.assertTrue(out["checks"]["stop_loss_depth"]["ok"])
        self.assertGreater(out["luck"]["z"], -2.0)

    def test_fail_when_win_rate_collapses(self):
        trades = [kb_trade("stop", -7.0)] * 19 + [kb_trade("hold20", 20.0)]  # 승률 5%
        out = V.judge_kr_breakout(kb_forward(trades), BT, CRIT["kr_breakout"])
        self.assertEqual(out["status"], V.FAIL)
        self.assertTrue(any("운 보정" in w for w in out["warnings"]))

    def test_fail_when_hold20_trades_start_losing(self):
        # 승률은 25% 로 살아 있지만 20일 보유 매매 6건 중 2승 → 생존 편향 신호
        trades = [kb_trade("stop", -7.0)] * 9 + [kb_trade("hold20", -3.0)] * 4 + [kb_trade("hold20", 15.0)] * 2 + [kb_trade("low10", 4.0)] * 3
        out = V.judge_kr_breakout(kb_forward(trades), BT, CRIT["kr_breakout"])
        self.assertEqual(out["status"], V.FAIL)
        self.assertIn("20일 보유", out["reason"])

    def test_deep_stops_hold_pass_back(self):
        trades = [kb_trade("stop", -12.0)] * 3 + [kb_trade("stop", -7.0)] * 11 + [kb_trade("hold20", 20.0)] * 6
        out = V.judge_kr_breakout(kb_forward(trades), BT, CRIT["kr_breakout"])
        self.assertEqual(out["status"], V.NOT_YET)
        self.assertFalse(out["checks"]["stop_loss_depth"]["ok"])


def venue(trades, bt_wr=37.0, bt_pf=1.8):
    return {"portfolio": {"forward_trades": trades,
                          "stats": {"win_rate_pct": bt_wr, "profit_factor": bt_pf, "trades": 111,
                                    "forward": {"trades": len(trades), "net_pct": 3.0 if trades else None}}},
            "variants": {"alt": {"portfolio": {"forward_trades": [], "stats": {"forward": {"trades": 0, "net_pct": None}}}}}}


def vt(pnl, r=1.0):
    return {"pnl": pnl, "r_multiple": r if pnl > 0 else -1.0, "reason": "trail" if pnl > 0 else "stop"}


class VenueTracks(unittest.TestCase):
    def test_not_yet_under_20(self):
        d = {"rules": {"rule_fixed": "2026-10-05"}, "venues": {"upbit": venue([vt(5)] * 19)}}
        out = V.judge_venue_track(d, CRIT["reversal"], "reversal")
        self.assertEqual(out["status"], V.NOT_YET)
        self.assertEqual(out["forward_trades_total"], 19)

    def test_reversal_pass_profit_factor(self):
        trades = [vt(30)] * 8 + [vt(-10)] * 12  # 손익비 2.0, 승률 40%
        d = {"rules": {}, "venues": {"upbit": venue(trades), "okx": venue(trades)}}
        out = V.judge_venue_track(d, CRIT["reversal"], "reversal")
        self.assertEqual(out["status"], V.PASS)
        self.assertEqual(out["venues"]["upbit"]["profit_factor"], 2.0)

    def test_reversal_fail_one_venue_fails_all(self):
        good = [vt(30)] * 8 + [vt(-10)] * 12
        bad = [vt(5)] * 5 + [vt(-10)] * 15  # 손익비 0.17
        d = {"rules": {}, "venues": {"upbit": venue(good), "okx": venue(bad)}}
        out = V.judge_venue_track(d, CRIT["reversal"], "reversal")
        self.assertEqual(out["status"], V.FAIL)
        self.assertEqual(out["venues"]["okx"]["status"], V.FAIL)

    def test_turtle_fail_on_luck_z(self):
        # 손익비는 1.3 으로 통과권이지만 승률 10% (백테스트 37%) → z 가 −2 아래
        trades = [vt(130)] * 2 + [vt(-10)] * 18  # 260/180 = 1.44
        d = {"rules": {}, "venues": {"upbit": venue(trades)}}
        out = V.judge_venue_track(d, CRIT["turtle"], "turtle")
        self.assertEqual(out["venues"]["upbit"]["status"], V.FAIL)
        self.assertIn("운 보정", out["venues"]["upbit"]["reason"])


def scalp_day(date, nt, base_nt=-1.0, key="okx:XRP:15m:vwap_fade_lim"):
    base_key = ":".join(key.split(":")[:3] + ["baseline_hold5"])
    return {"date": date, "rows": {key: {"n": 3, "g": 0, "f": 0, "nt": nt, "rb": False},
                                   base_key: {"n": 10, "g": 0, "f": 0, "nt": base_nt, "rb": False}}}


class Scalp(unittest.TestCase):
    """누적 기록(scalp_history)에 scalp.py 의 후보 규칙을 적용하는지 본다. 하루치 견고 표시는 쓰지 않는다."""
    c = CRIT["scalp"]

    def test_not_yet_under_min_days(self):
        rows = [scalp_day("2026-10-0%d" % i, 1.0) for i in range(1, 7)]
        self.assertEqual(V.judge_scalp(rows, self.c)["status"], V.NOT_YET)

    def test_pass_on_cumulative_rule(self):
        # 9일 중 6일 플러스, 누적 +3, 기준선(−9) 초과 → 후보
        nts = [1.0, 1.0, -0.5, 1.0, -0.5, 1.0, 0.5, -0.5, 1.0]
        rows = [scalp_day("2026-10-%02d" % (i + 1), x) for i, x in enumerate(nts)]
        out = V.judge_scalp(rows, self.c)
        self.assertEqual(out["status"], V.PASS)
        self.assertEqual(out["candidates"], 1)
        self.assertEqual(out["combos"], 1)
        self.assertTrue(out["warnings"])

    def test_fail_when_too_few_positive_days(self):
        # 누적은 플러스지만 9일 중 4일만 플러스(44%) → 후보 아님
        nts = [5.0, -0.5, -0.5, 1.0, -0.5, 1.0, -0.5, 1.0, -0.5]
        rows = [scalp_day("2026-10-%02d" % (i + 1), x) for i, x in enumerate(nts)]
        self.assertEqual(V.judge_scalp(rows, self.c)["status"], V.FAIL)

    def test_one_day_robust_flag_is_ignored(self):
        # 하루치 rb=True 가 있어도 누적 규칙을 못 넘으면 불합격
        rows = [scalp_day("2026-10-%02d" % (i + 1), -0.3) for i in range(8)]
        rows[-1]["rows"]["okx:XRP:15m:vwap_fade_lim"]["rb"] = True
        self.assertEqual(V.judge_scalp(rows, self.c)["status"], V.FAIL)


class NoiseFloor(unittest.TestCase):
    """제안함 P17 — 후보 수가 '실력 0 이어도 뜨는 후보 수(중앙값)' 이하면 PASS 를 주지 않는다."""
    c = CRIT["scalp"]

    @staticmethod
    def many(n_cand, n_total=40, days=7):
        rows = [{"date": "2026-10-%02d" % (i + 1), "rows": {}} for i in range(days)]
        for j in range(n_total):
            key = "okx:C%02d:15m:vwap_fade" % j
            base = "okx:C%02d:15m:baseline_hold5" % j
            # 후보: 7일 중 5일 +0.1, 2일 −0.1 (누적 +0.3, 71% 플러스). 비후보: 전부 −0.1
            nts = [0.1, 0.1, -0.1, 0.1, 0.1, -0.1, 0.1] if j < n_cand else [-0.1] * days
            for i in range(days):
                rows[i]["rows"][key] = {"n": 3, "g": 0, "f": 0, "nt": nts[i], "rb": False}
                rows[i]["rows"][base] = {"n": 10, "g": 0, "f": 0, "nt": -1.0, "rb": False}
        return rows

    def test_noise_floor_is_computed_and_deterministic(self):
        nf1 = V.noise_floor(self.many(3), sims=200, seed=7)
        nf2 = V.noise_floor(self.many(3), sims=200, seed=7)
        self.assertEqual(nf1["median"], nf2["median"])
        # 실력 0 이면 조합 40개 중 대략 20~25% 가 후보로 뜬다 (7일 중 5일 이상 플러스 확률 29/128)
        self.assertTrue(4 <= nf1["median"] <= 15, nf1)

    def test_candidates_below_noise_floor_is_not_pass(self):
        out = V.judge_scalp(self.many(3), self.c)
        self.assertEqual(out["candidates"], 3)
        self.assertLessEqual(out["candidates"], out["noise_floor"]["median"])
        self.assertEqual(out["status"], V.NOT_YET)
        self.assertIn("잡음 바닥", out["reason"])

    def test_candidates_above_noise_floor_pass(self):
        out = V.judge_scalp(self.many(40), self.c)
        self.assertEqual(out["candidates"], 40)
        self.assertGreater(out["candidates"], out["noise_floor"]["median"])
        self.assertEqual(out["status"], V.PASS)

    def test_fail_still_reports_noise_floor(self):
        out = V.judge_scalp(self.many(0), self.c)
        self.assertEqual(out["status"], V.FAIL)
        self.assertIn("noise_floor", out)


class Interpretation(unittest.TestCase):
    """제안함 P18 — 합격선을 '실력 0 이어도 PASS 할 확률'과 '필요 건수'로 번역. 합격선 숫자는 건드리지 않는다."""

    def test_turtle_upbit_numbers(self):
        # 백테스트 승률 37.3%, 손익비 1.65 → 합격선 PF≥1.2 @20건, z≥−2 (docs/BACKTEST_OVERFITTING_REVIEW.md 3절)
        ip = V.interpret_pf_rule(20, 0.372881, 1.6455, 1.2, -2.0)
        self.assertTrue(20 <= ip["false_pass_pct"] <= 32, ip)
        self.assertEqual(ip["min_trades_95"], 55)
        self.assertTrue(ip["min_trades_t3"] > ip["min_trades_95"])

    def test_binom_tail_edges(self):
        self.assertEqual(V.binom_tail(10, 0.3, 0), 1.0)
        self.assertEqual(V.binom_tail(10, 0.3, 11), 0.0)
        self.assertAlmostEqual(V.binom_tail(1, 0.3, 1), 0.3)

    def test_kr_breakout_shape_test(self):
        ip = V.interpret_win_rate_rule(15, 0.2935, 3)
        self.assertTrue(80 <= ip["pass_pct_if_backtest_true"] <= 92, ip)
        self.assertTrue(30 <= ip["pass_pct_if_win_rate_halved"] <= 45, ip)

    def test_none_without_backtest(self):
        self.assertIsNone(V.interpret_pf_rule(20, None, None, 1.2))

    def test_venue_track_carries_interpretation(self):
        data = {"rules": {"rule_fixed": "2026-10-02"},
                "venues": {"okx": {"portfolio": {"forward_trades": [], "stats": {"win_rate_pct": 34.85, "profit_factor": 1.23, "trades": 241}}}}}
        out = V.judge_venue_track(data, CRIT["turtle"], "turtle")
        self.assertIn("false_pass_pct", out["venues"]["okx"]["interpretation"])


class RulesLock(unittest.TestCase):
    def test_lock_matches_source(self):
        """규칙 상수나 criteria.json 이 잠금과 다르면 실패한다. 고치는 길은 하나: proposals.md → RULE_FIXED → `python verdict.py --lock`."""
        diffs = V.check_lock()
        self.assertEqual(diffs, [], "\n규칙 잠금 불일치 — 사전 등록 없이 규칙이 바뀌었다:\n  " + "\n  ".join(diffs))

    def test_detects_change(self):
        cur = V.current_rules()
        lock = json.loads(json.dumps(cur))
        lock["turtle"]["rules"]["entry_breakout_days"] = 55
        diffs = V.check_lock(cur, lock)
        self.assertEqual(len(diffs), 1)
        self.assertIn("turtle/rules/entry_breakout_days", diffs[0])


class History(unittest.TestCase):
    def test_same_date_overwritten(self):
        out = {"date": "2026-10-06", "summary": {"scalp": "FAIL"}, "rules_lock": {"ok": True},
               "tracks": {"scalp": {"history_days": 8, "candidates": 0}}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            V.write_history(out, p)
            out2 = dict(out, tracks={"scalp": {"history_days": 9, "candidates": 1}})
            V.write_history(out2, p)
            rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scalp"]["candidates"], 1)


if __name__ == "__main__":
    unittest.main()
