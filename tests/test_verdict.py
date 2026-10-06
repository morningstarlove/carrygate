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


class Scalp(unittest.TestCase):
    def test_states(self):
        c = CRIT["scalp"]
        self.assertEqual(V.judge_scalp({"summary": {"history_days": 3, "rows_robust": 0}}, c)["status"], V.NOT_YET)
        self.assertEqual(V.judge_scalp({"summary": {"history_days": 8, "rows_robust": 0}}, c)["status"], V.FAIL)
        self.assertEqual(V.judge_scalp({"summary": {"history_days": 8, "rows_robust": 2}}, c)["status"], V.PASS)


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
               "tracks": {"scalp": {"history_days": 8, "rows_robust": 0}}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "h.jsonl")
            V.write_history(out, p)
            out2 = dict(out, tracks={"scalp": {"history_days": 9, "rows_robust": 1}})
            V.write_history(out2, p)
            rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["scalp"]["robust"], 1)


if __name__ == "__main__":
    unittest.main()
