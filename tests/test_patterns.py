# -*- coding: utf-8 -*-
"""
patterns.py (박스권·삼각수렴·깃발·컵앤핸들) 검증 — 가짜 봉 + 저장된 실데이터 일봉, 인터넷 없음.

  python3 -m unittest tests.test_patterns -v
"""
import os, sys, math, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import patterns as P  # noqa: E402
import kr_ohlcv  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OHLCV = os.path.join(HERE, "fixtures", "kr", "ohlcv")


def bar(i, o, h, l, c, v=1_000_000):
    # 가짜 봉은 가격이 100원대라 거래대금을 따로 넉넉히 준다 (거래대금 필터는 실데이터 시험이 본다)
    return {"date": "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), "o": o, "h": h, "l": l, "c": c, "v": v, "value": 2e10}


def flat(n, price=100.0, v=1_000_000, start=0):
    """잔잔한 횡보 n봉 (거래량 평균을 잡아 준다)."""
    return [bar(start + i, price, price * 1.01, price * 0.99, price, v) for i in range(n)]


def box_bars(n_box=20, price=100.0, width=0.10, v=1_000_000):
    """상단·하단을 번갈아 찍는 박스 n_box 봉."""
    top, bottom = price * (1 + width / 2), price * (1 - width / 2)
    out = []
    for i in range(n_box):
        if i % 4 == 0:
            out.append(bar(i, price, top, price * 0.995, price * 1.01, v))
        elif i % 4 == 2:
            out.append(bar(i, price, price * 1.005, bottom, price * 0.99, v))
        else:
            out.append(bar(i, price, price * 1.01, price * 0.99, price, v))
    return out


class Box(unittest.TestCase):
    def test_breakout_with_volume(self):
        bars = flat(45) + box_bars(20) + [bar(70, 105, 112, 104, 111, 2_000_000)]   # 상단 105 돌파, 거래량 2배
        r = P.scan(bars)
        self.assertTrue(r["ok"], r)
        pats = {p["pattern"]: p for p in r["patterns"]}
        self.assertIn("box", pats)
        self.assertAlmostEqual(pats["box"]["resistance"], 105.0)
        self.assertAlmostEqual(pats["box"]["pattern_low"], 95.0)

    def test_no_breakout_without_volume(self):
        bars = flat(45) + box_bars(20) + [bar(70, 105, 112, 104, 111, 1_200_000)]   # 1.2배뿐
        r = P.scan(bars)
        self.assertFalse(r["ok"]); self.assertIn("거래량", r["reason"])
        self.assertEqual([p["pattern"] for p in r["patterns"]], ["box"])     # 패턴은 있으나 필터 탈락

    def test_close_below_top_is_not_breakout(self):
        bars = flat(45) + box_bars(20) + [bar(70, 103, 106, 102, 104.5, 3_000_000)]  # 고가는 넘었지만 종가는 아래
        self.assertFalse(any(p["pattern"] == "box" for p in P.scan(bars)["patterns"]))

    def test_too_wide_is_not_box(self):
        bars = flat(45) + box_bars(20, width=0.40) + [bar(70, 121, 130, 120, 128, 3_000_000)]
        self.assertFalse(any(p["pattern"] == "box" for p in P.scan(bars)["patterns"]))

    def test_limit_up_excluded(self):
        bars = flat(45) + box_bars(20) + [bar(70, 105, 130, 104, 130, 3_000_000)]   # +30%
        r = P.scan(bars)
        self.assertFalse(r["ok"]); self.assertIn("상한가", r["reason"])
        self.assertTrue(r["filters"]["limit_up"])

    def test_below_sma20_excluded(self):
        down = [bar(i, 200 - i, 201 - i, 198 - i, 199 - i) for i in range(60)]        # 계속 내려오는 중
        r = P.scan(down + [bar(60, 140, 150, 130, 135, 3_000_000)])
        self.assertFalse(r["ok"]); self.assertEqual(r["reason"], "20일선 아래")


class Flag(unittest.TestCase):
    def _flag(self, flag_vol=300_000, retrace=0.2, breakout=True, today_vol=2_500_000):
        base = flat(60, 100.0, 1_000_000)
        pole = [bar(60 + i, 100 + 5 * i, 106 + 5 * i, 99 + 5 * i, 105 + 5 * i, 3_000_000) for i in range(5)]  # 100→~130 (+30%)
        top = pole[-1]["h"]; low = pole[0]["l"]
        fl_low = top - retrace * (top - low)
        fl = [bar(65 + i, top - 2, top - 1, fl_low, top - 2, flag_vol) for i in range(6)]
        today_c = top + 3 if breakout else top - 2
        return base + pole + fl + [bar(71, top - 1, today_c + 1, top - 3, today_c, today_vol)]

    def test_flag_breakout(self):
        r = P.scan(self._flag())
        self.assertTrue(r["ok"], r)
        f = [p for p in r["patterns"] if p["pattern"] == "flag"][0]
        self.assertGreaterEqual(f["pole_rise_pct"], 20)
        self.assertLessEqual(f["flag_vol_ratio"], 0.7)

    def test_flag_needs_quiet_volume(self):
        r = P.scan(self._flag(flag_vol=2_800_000))                                   # 깃발 거래량이 깃대 수준
        self.assertFalse(any(p["pattern"] == "flag" for p in r["patterns"]))

    def test_flag_deep_retrace_rejected(self):
        r = P.scan(self._flag(retrace=0.7))
        self.assertFalse(any(p["pattern"] == "flag" for p in r["patterns"]))

    def test_flag_no_breakout(self):
        r = P.scan(self._flag(breakout=False))
        self.assertFalse(any(p["pattern"] == "flag" for p in r["patterns"]))


class Triangle(unittest.TestCase):
    def _tri(self, breakout=True):
        base = flat(60, 100.0)
        out = list(base)
        # 40봉 동안 고점은 120→102, 저점은 80→98 로 수렴 (4봉 주기 지그재그)
        for i in range(40):
            hi = 120 - 0.45 * i; lo = 80 + 0.45 * i; mid = (hi + lo) / 2
            if i % 4 == 0:
                out.append(bar(60 + i, mid, hi, mid - 1, mid + 1))
            elif i % 4 == 2:
                out.append(bar(60 + i, mid, mid + 1, lo, mid - 1))
            else:
                out.append(bar(60 + i, mid, mid + 2, mid - 2, mid))
        hi_now = 120 - 0.45 * 40
        c = hi_now + 4 if breakout else hi_now - 3
        out.append(bar(100, hi_now, c + 1, hi_now - 2, c, 2_500_000))
        return out

    def test_triangle_breakout(self):
        r = P.scan(self._tri())
        self.assertTrue(r["ok"], r)
        t = [p for p in r["patterns"] if p["pattern"] == "triangle"][0]
        self.assertGreaterEqual(t["pivot_highs"], 2); self.assertGreaterEqual(t["pivot_lows"], 2)
        self.assertLessEqual(t["contraction"], 0.5)
        self.assertGreater(t["apex_in_bars"], 0)

    def test_triangle_no_breakout(self):
        r = P.scan(self._tri(breakout=False))
        self.assertFalse(any(p["pattern"] == "triangle" for p in r["patterns"]))


class CupHandle(unittest.TestCase):
    def _cup(self, depth=0.25, handle_depth=0.05, breakout=True):
        base = flat(30, 100.0)
        n = 60
        cup = []
        for i in range(n):
            x = (i / (n - 1)) * 2 - 1                       # -1..1
            price = 100 - 100 * depth * (1 - x * x)         # U 자
            cup.append(bar(30 + i, price, price + 1, price - 1, price))
        rim = max(b["h"] for b in cup[-12:])
        handle = [bar(90 + i, rim - 1, rim - 0.5, rim * (1 - handle_depth), rim - 1, 500_000) for i in range(8)]
        c = rim + 2 if breakout else rim - 1
        return base + cup + handle + [bar(98, rim, c + 1, rim - 1, c, 3_000_000)]

    def test_cup_handle_breakout(self):
        r = P.scan(self._cup())
        self.assertTrue(r["ok"], r)
        c = [p for p in r["patterns"] if p["pattern"] == "cup_handle"][0]
        self.assertTrue(12 <= c["cup_depth_pct"] <= 35)
        self.assertLessEqual(c["handle_depth_pct"], 12)

    def test_cup_too_shallow(self):
        r = P.scan(self._cup(depth=0.06))
        self.assertFalse(any(p["pattern"] == "cup_handle" for p in r["patterns"]))

    def test_handle_too_deep(self):
        r = P.scan(self._cup(depth=0.25, handle_depth=0.15))
        self.assertFalse(any(p["pattern"] == "cup_handle" for p in r["patterns"]))

    def test_cup_no_breakout(self):
        r = P.scan(self._cup(breakout=False))
        self.assertFalse(any(p["pattern"] == "cup_handle" for p in r["patterns"]))


class Helpers(unittest.TestCase):
    def test_atr_and_sma(self):
        bars = flat(30, 100.0)
        a = P.atr(bars)
        self.assertIsNone(a[18]); self.assertAlmostEqual(a[19], 2.0, places=6); self.assertAlmostEqual(a[29], 2.0, places=6)
        self.assertAlmostEqual(P.sma([1, 2, 3, 4], 2)[3], 3.5)

    def test_too_few_bars(self):
        r = P.scan(flat(20))
        self.assertFalse(r["ok"]); self.assertIn("봉 부족", r["reason"])


@unittest.skipUnless(os.path.exists(os.path.join(OHLCV, "058470.csv")), "실데이터 fixture 없음")
class RealData(unittest.TestCase):
    """설계서 1절의 세 사례가 실데이터(네이버 일봉, Actions 수집)로 재현돼야 한다."""

    def _hits(self, code):
        bars = kr_ohlcv.load_csv(os.path.join(OHLCV, code + ".csv"))
        return {h["date"]: h for h in P.scan_history(bars)}

    def test_leeno_box_2026_09_22(self):
        h = self._hits("058470")
        self.assertIn("2026-09-22", h)
        box = [p for p in h["2026-09-22"]["patterns"] if p["pattern"] == "box"][0]
        self.assertAlmostEqual(box["resistance"], 73400.0)
        self.assertGreaterEqual(h["2026-09-22"]["filters"]["vol_ratio"], 3.0)

    def test_jeju_semi_box_2026_10_01(self):
        h = self._hits("080220")
        self.assertIn("2026-10-01", h)
        self.assertIn("box", [p["pattern"] for p in h["2026-10-01"]["patterns"]])
        self.assertGreaterEqual(h["2026-10-01"]["filters"]["vol_ratio"], 3.0)

    def test_sungho_flag_2026_10_02(self):
        h = self._hits("043260")
        self.assertIn("2026-10-02", h)
        fl = [p for p in h["2026-10-02"]["patterns"] if p["pattern"] == "flag"][0]
        self.assertGreaterEqual(fl["pole_rise_pct"], 20)

    def test_samsung_no_signal(self):
        # 삼성전자는 같은 기간 패턴은 보여도 거래량 1.5배를 못 넘어 신호가 없어야 한다
        self.assertEqual(len(self._hits("005930")), 0)


if __name__ == "__main__":
    unittest.main()
