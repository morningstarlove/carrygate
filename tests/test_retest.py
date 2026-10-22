# -*- coding: utf-8 -*-
"""
retest.py 계산 로직 검증 — 가짜 1시간봉, 인터넷 없음.

  python3 -m unittest tests.test_retest -v
"""
import os, sys, math, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import retest  # noqa: E402

T0 = 1_700_000_000


class Path:
    """봉을 차례로 쌓는 도우미(추세전환 시험과 같은 규칙). 오르는 봉: 고가 = 종가+1, 저가 = 시가−0.5 / 내리는 봉: 고가 = 시가+0.5, 저가 = 종가−1."""

    def __init__(self, start):
        self.bars = []
        self.last = float(start)

    def add(self, o, h, l, c, v=100.0):
        self.bars.append(retest._mk(T0 + len(self.bars) * 3600, o, h, l, c, v))
        self.last = c
        return self

    def flat(self, n, v=100.0):
        for _ in range(n):
            self.add(self.last, self.last + 0.5, self.last - 0.5, self.last, v)
        return self

    def leg(self, to, n, v=100.0):
        frm = self.last
        for k in range(1, n + 1):
            c = frm + (to - frm) * k / n
            o = self.last
            if c >= o:
                self.add(o, c + 1.0, o - 0.5, c, v)
            else:
                self.add(o, o + 0.5, c - 1.0, c, v)
        return self


def card_path():
    """카드의 급상승 패턴: 평소 100(30봉, 거래량 100) → ① 1차 상승 100→130 8봉(거래량 200) → ② 횡보 124~128 10봉(거래량 60)
    → ③ 돌파 종가 131 → 2차 상승 →140 6봉(거래량 120) → ④ 조정 →127 6봉(거래량 50) → ⑤ 확인 양봉(저가 126.5, 종가 129.5).
    지지 구간 = [횡보 최저가 123.5, 횡보 최고가 129(= 1차 상승 마지막 봉 고가 131? 아니다: B 는 피벗 고점 봉 = 1차 상승 마지막 봉, 고가 131)]."""
    p = Path(100)
    p.flat(30)
    p.leg(130, 8, v=200)                                   # A = 봉 30(저가 99.5), B = 봉 37 (고가 131)
    for k in range(10):                                    # 횡보: 종가 124~128 번갈아, 고가·저가 폭 작게
        c = 124.0 if k % 2 == 0 else 128.0
        p.add(p.last, max(p.last, c) + 0.5, min(p.last, c) - 0.5, c, 60)
    p.add(p.last, 132.0, 127.5, 131.5, 100)               # 돌파: 종가 131.5 > 횡보 최고가 131
    return p


def rise2_and_pullback(p):
    p.leg(140, 6, v=120)                                   # 2차 상승, 고점 봉 고가 141
    p.leg(127, 6, v=50)                                    # 조정 (거래량 감소), 저가 126
    return p


def confirm(p, v=80.0):
    return p.add(127, 130, 126.5, 129.5, v)                # 양봉, 윗절반 마감, 저가 ≤ 지지 상단


def zone_of(bars):
    """시험용: 횡보 10봉 + B 봉 고가로 계산한 지지 구간."""
    box = bars[38:48]
    return min(b["l"] for b in box), max([bars[37]["h"]] + [b["h"] for b in box])


class Structure(unittest.TestCase):
    def test_find_pole(self):
        bars = card_path().bars
        atr = retest.wilder_atr(bars)
        b = 37
        self.assertTrue(retest.is_pivot_high(bars, b))
        pole = retest.find_pole(bars, b, atr[b])
        self.assertIsNotNone(pole)
        self.assertEqual(pole["a"], 30)                     # 같은 저가(99.5)면 B 에 가까운 봉
        self.assertAlmostEqual(pole["height"], 131.0 - 99.5)
        self.assertGreater(pole["pole_vol"], 1.5 * 100.0)

    def test_pole_needs_volume_and_height(self):
        p = Path(100).flat(30).leg(130, 8, v=100)          # 거래량 평소와 같음
        bars = p.flat(4).bars
        atr = retest.wilder_atr(bars)
        self.assertIsNone(retest.find_pole(bars, 37, atr[37]))
        self.assertIsNotNone(retest.find_pole(bars, 37, atr[37], vol=False))
        q = Path(100).flat(30).leg(102, 8, v=200).flat(4)  # 높이 ATR×3 미만
        bars = q.bars
        atr = retest.wilder_atr(bars)
        self.assertIsNone(retest.find_pole(bars, 37, atr[37]))

    def test_confirm_bar(self):
        self.assertTrue(retest.is_confirm_bar({"o": 127, "h": 130, "l": 126.5, "c": 129.5}, 123.5, 129.0))
        self.assertFalse(retest.is_confirm_bar({"o": 130, "h": 131, "l": 129.5, "c": 130.5}, 123.5, 129.0))   # 구간에 안 닿음
        self.assertFalse(retest.is_confirm_bar({"o": 129, "h": 130, "l": 125, "c": 127}, 123.5, 129.0))       # 음봉
        self.assertFalse(retest.is_confirm_bar({"o": 125, "h": 131, "l": 124, "c": 126}, 123.5, 129.0))       # 아랫절반 마감
        self.assertFalse(retest.is_confirm_bar({"o": 122, "h": 124, "l": 121, "c": 123}, 123.5, 129.0))       # 지지 하단 아래 마감

    def test_mirror(self):
        bars = card_path().bars
        m = retest.mirror(bars)
        self.assertEqual(m[5]["h"], -bars[5]["l"])
        self.assertEqual(m[5]["l"], -bars[5]["h"])
        self.assertEqual(m[5]["v"], bars[5]["v"])


class Backtest(unittest.TestCase):
    def _full(self):
        p = card_path()
        rise2_and_pullback(p)
        confirm(p)
        p.leg(150, 8).leg(140, 4).leg(165, 8).leg(130, 8)   # 상승 → 눌림(피벗 저점 139) → 상승 → 하락(저점 이탈)
        return p.bars

    def test_full_long_trade(self):
        bars = self._full()
        zone_lo, zone_hi = zone_of(bars)
        res = retest.run({"X": bars}, 0.0, 10_000.0, slip_pct=0.0)
        self.assertEqual(res["counts"]["poles"], 1)
        self.assertEqual(res["counts"]["breakouts"], 1)
        self.assertEqual(len(res["trades"]), 1)
        t = res["trades"][0]
        self.assertEqual(t["dir"], "long")
        self.assertEqual(t["entry_how"], "confirm_close")
        self.assertAlmostEqual(t["entry"], 129.5)
        self.assertAlmostEqual(t["zone_lo"], zone_lo)
        self.assertAlmostEqual(t["zone_hi"], zone_hi)
        self.assertAlmostEqual(t["stop0"], zone_lo)
        self.assertAlmostEqual(t["units"], 10_000 * 0.01 / (129.5 - zone_lo))
        self.assertEqual(t["reason"], "trail")
        self.assertGreaterEqual(t["stop_raises"], 1)
        self.assertAlmostEqual(t["stop_final"], 139.0)
        self.assertAlmostEqual(t["exit"], 139.0)
        self.assertGreater(t["pnl"], 0)
        self.assertAlmostEqual(math.prod(1 + x["ret_pct"] / 100 for x in res["trades"]) * 10_000, res["equity"], places=6)

    def test_short_is_mirror_of_long(self):
        """같은 길을 200 을 축으로 뒤집으면 숏 매매가 같은 손익으로 나와야 한다 (수수료 0)."""
        bars = self._full()
        flipped = [dict(b, o=200 - b["o"], h=200 - b["l"], l=200 - b["h"], c=200 - b["c"]) for b in bars]
        lr = retest.run({"X": bars}, 0.0, 10_000.0, slip_pct=0.0)
        sr = retest.run({"X": {"bars": flipped, "dir": "short"}}, 0.0, 10_000.0, slip_pct=0.0)
        self.assertEqual(len(sr["trades"]), 1)
        s, l = sr["trades"][0], lr["trades"][0]
        self.assertEqual(s["dir"], "short")
        self.assertAlmostEqual(s["entry"], 200 - l["entry"])
        self.assertAlmostEqual(s["exit"], 200 - l["exit"])
        self.assertAlmostEqual(s["stop0"], 200 - l["stop0"])
        self.assertAlmostEqual(s["zone_lo"], 200 - l["zone_hi"])
        self.assertAlmostEqual(s["zone_hi"], 200 - l["zone_lo"])
        self.assertAlmostEqual(s["pnl"], l["pnl"], places=6)
        self.assertAlmostEqual(s["mfe_pct"] * s["entry"], l["mfe_pct"] * l["entry"], places=3)

    def test_short_slippage_is_adverse(self):
        """숏도 슬리피지는 불리한 쪽: 진입은 확인 봉 종가보다 낮게 팔고, 손절 청산은 손절선보다 높게 산다 (10/22 dry_run 검산이 잡은 버그)."""
        bars = self._full()
        flipped = [dict(b, o=200 - b["o"], h=200 - b["l"], l=200 - b["h"], c=200 - b["c"]) for b in bars]
        p = card_path(); rise2_and_pullback(p); confirm(p); p.leg(120, 4)
        flipped_stop = [dict(b, o=200 - b["o"], h=200 - b["l"], l=200 - b["h"], c=200 - b["c"]) for b in p.bars]
        for data, reason in ((flipped, "trail"), (flipped_stop, "stop")):
            sr = retest.run({"X": {"bars": data, "dir": "short"}}, 0.0, 10_000.0, slip_pct=0.1)
            self.assertEqual(len(sr["trades"]), 1)
            t = sr["trades"][0]
            self.assertEqual(t["reason"], reason)
            self.assertLess(t["entry"], 200 - 129.5)                    # 확인 봉 종가(70.5)보다 낮게 체결
            self.assertAlmostEqual(t["entry"], 70.5 * (1 - 0.001))
            self.assertGreaterEqual(t["exit"], t["stop_final"])          # 손절 청산가는 손절선 이상(불리)
            lr = retest.run({"X": bars if reason == "trail" else p.bars}, 0.0, 10_000.0, slip_pct=0.1)
            self.assertAlmostEqual(lr["trades"][0]["exit"] + t["exit"], 200.0, places=6)   # 롱·숏 대칭

    def test_no_confirm_bar_no_entry_then_expire(self):
        p = card_path()
        rise2_and_pullback(p)
        for k in range(50):                                 # 조정 뒤 음봉만 이어지다 기한 만료 (지지 하단 위에서)
            p.add(127.5, 128.0, 126.5, 127.0, 40)
        res = retest.run({"X": p.bars}, 0.0, 10_000.0)
        self.assertEqual(res["trades"], [])
        self.assertIsNone(res["state"]["X"]["pos"])
        self.assertEqual(res["counts"]["no_pullback"], 1)

    def test_zone_limit_variant_enters_on_touch(self):
        p = card_path()
        rise2_and_pullback(p)
        bars = p.bars
        zone_lo, zone_hi = zone_of(bars)
        base = retest.run({"X": bars}, 0.0, 10_000.0)
        self.assertIsNone(base["state"]["X"]["pos"])        # 기본: 확인 봉 전이라 아직 안 들어감
        self.assertEqual(base["state"]["X"]["phase"], "pullback")
        lim = retest.run({"X": bars}, 0.0, 10_000.0, entry="limit")
        pos = lim["state"]["X"]["pos"]
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["entry"], zone_hi)       # 지지 상단 지정가
        self.assertEqual(pos["how"], "zone_limit")

    def test_volume_rules_block_and_no_vol_variant_allows(self):
        p = Path(100).flat(30).leg(130, 8, v=100)           # 1차 상승 거래량 평소와 같음 → 기본은 구조 없음
        for k in range(10):
            c = 124.0 if k % 2 == 0 else 128.0
            p.add(p.last, max(p.last, c) + 0.5, min(p.last, c) - 0.5, c, 60)
        p.add(p.last, 132.0, 127.5, 131.5, 100)
        rise2_and_pullback(p)
        confirm(p)
        p.leg(150, 8)
        base = retest.run({"X": p.bars}, 0.0, 10_000.0)
        self.assertEqual(base["counts"]["poles"], 0)
        nov = retest.run({"X": p.bars}, 0.0, 10_000.0, vol=False)
        self.assertEqual(nov["counts"]["poles"], 1)
        self.assertIsNotNone(nov["state"]["X"]["pos"])

    def test_pullback_volume_must_shrink(self):
        p = card_path()
        p.leg(140, 6, v=120)
        p.leg(127, 6, v=200)                                # 조정 거래량이 2차 상승보다 크다 → 확인 봉이 와도 안 들어감
        confirm(p, v=200)
        p.leg(150, 8)
        base = retest.run({"X": p.bars}, 0.0, 10_000.0)
        self.assertIsNone(base["state"]["X"]["pos"])
        nov = retest.run({"X": p.bars}, 0.0, 10_000.0, vol=False)
        self.assertIsNotNone(nov["state"]["X"]["pos"])

    def test_zone_lost_invalidates(self):
        p = card_path()
        rise2_and_pullback(p)
        p.leg(115, 6)                                       # 지지 하단(123.5) 아래로 종가
        confirm(p)
        res = retest.run({"X": p.bars}, 0.0, 10_000.0)
        self.assertEqual(res["trades"], [])
        self.assertEqual(res["counts"]["zone_lost"], 1)
        self.assertEqual(res["state"]["X"]["phase"], "scan")

    def test_box_retrace_over_half_fails(self):
        p = card_path().bars[:38]                           # 1차 상승까지
        q = Path(130)
        q.bars = list(p)
        q.last = 130.0
        q.leg(110, 6)                                       # 횡보 대신 절반 넘게 되돌림
        q.flat(10)
        res = retest.run({"X": q.bars}, 0.0, 10_000.0)
        self.assertEqual(res["counts"]["poles"], 1)
        self.assertEqual(res["counts"]["box_failed"], 1)
        self.assertEqual(res["counts"]["breakouts"], 0)

    def test_target_variant_takes_profit(self):
        p = card_path()
        rise2_and_pullback(p)
        confirm(p)
        p.leg(150, 8).flat(5)
        bars = p.bars
        zone_lo, _ = zone_of(bars)
        res = retest.run({"X": bars}, 0.0, 10_000.0, slip_pct=0.0, target_r=2.0)
        self.assertEqual(len(res["trades"]), 1)
        t = res["trades"][0]
        self.assertEqual(t["reason"], "target")
        self.assertAlmostEqual(t["target"], 129.5 + 2.0 * (129.5 - zone_lo))
        self.assertGreaterEqual(t["exit"], t["target"])

    def test_stop_hit_before_any_raise(self):
        p = card_path()
        rise2_and_pullback(p)
        confirm(p)
        p.leg(120, 4)                                       # 바로 지지 하단 이탈
        res = retest.run({"X": p.bars}, 0.0, 10_000.0, slip_pct=0.0)
        self.assertEqual(len(res["trades"]), 1)
        t = res["trades"][0]
        self.assertEqual(t["reason"], "stop")
        self.assertLessEqual(t["exit"], t["stop0"])
        self.assertLess(t["pnl"], 0)
        self.assertAlmostEqual(t["r_multiple"], -1.0, places=1)

    def test_shared_account_caps_notional(self):
        bars = self._full()
        res = retest.run({"X": bars, "Y": bars}, 0.0, 10_000.0, slip_pct=0.0, max_notional_pct=30.0)
        self.assertEqual(len(res["trades"]), 2)                                   # 둘째 시장은 남은 한도만큼만
        self.assertLessEqual(sum(t["units"] * t["entry"] for t in res["trades"]), 10_000.0 * 0.30 + 1e-6)
        self.assertLess(res["trades"][1]["units"], res["trades"][0]["units"])


class Reporting(unittest.TestCase):
    def test_signal_phases(self):
        p = card_path()
        res = retest.run({"X": p.bars}, 0.0, 10_000.0)
        sg = retest.signal(p.bars, res["state"]["X"], 10_000.0)
        self.assertEqual(sg["phase"], "rise2")
        self.assertIn("돌파 확인", sg["action"])
        rise2_and_pullback(p)
        res = retest.run({"X": p.bars}, 0.0, 10_000.0)
        sg = retest.signal(p.bars, res["state"]["X"], 10_000.0)
        self.assertEqual(sg["phase"], "pullback")
        self.assertIn("조정 중", sg["action"])
        self.assertIsNotNone(sg["units_if_fill_at_zone_edge"])
        confirm(p)
        res = retest.run({"X": p.bars}, 0.0, 10_000.0)
        sg = retest.signal(p.bars, res["state"]["X"], 10_000.0)
        self.assertEqual(sg["position"], "long")
        self.assertIn("보유", sg["action"])

    def test_evaluate_and_stats(self):
        bars = Backtest()._full()
        cfg = {"fee_pct": 0.05, "equity": 10_000.0, "dirs": ("long", "short")}
        ev = retest.evaluate({"BTC": bars}, cfg)
        self.assertIn("BTC/long", ev["markets"])
        self.assertIn("BTC/short", ev["markets"])
        st = ev["markets"]["BTC/long"]["stats"]
        self.assertEqual(st["trades"], 1)
        self.assertEqual(st["longs"], 1)
        self.assertIn("buy_hold_pct", st)
        self.assertEqual(ev["portfolio"]["stats"]["trades"], 1)
        net = math.prod(1 + r / 100 for r in ev["portfolio"]["returns_pct"])
        self.assertAlmostEqual((net - 1) * 100, ev["portfolio"]["stats"]["realized_net_pct"], places=3)

    def test_rules_dict_matches_constants(self):
        r = retest.rules_dict()
        self.assertEqual(r["bar_minutes"], 60)
        self.assertEqual(set(r["variants"]), set(retest.VARIANTS))
        self.assertEqual(r["dirs"]["upbit"], ["long"])
        self.assertEqual(r["dirs"]["okx"], ["long", "short"])

    def test_history_overwrites_same_date(self):
        import tempfile, json
        out = {"date": "2026-10-22", "summary": {"ok": True}, "venues": {"okx": {"markets": {"BTC/long": {"signal": {"phase": "scan"}}}}}}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "h.jsonl")
            retest.write_history(out, path)
            out2 = dict(out, summary={"ok": False})
            retest.write_history(out2, path)
            rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["summary"]["ok"])
            self.assertEqual(rows[0]["signals"]["okx/BTC/long"]["phase"], "scan")


if __name__ == "__main__":
    unittest.main()
