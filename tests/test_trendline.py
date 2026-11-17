# -*- coding: utf-8 -*-
"""추세선 채널(trendline.py, 제안함 P39) 단위시험. 인터넷 불필요."""
import os, sys, csv, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import trendline as T

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def zigzag(n=200, slope=0.5, width=20.0, period=40, base=100.0):
    """기울기 slope 의 하단선 위에서 폭 width 로 오르내리는 인공 봉. 저점은 정확히 하단선, 고점은 정확히 상단선."""
    bars = []
    for i in range(n):
        ph = (i % period) / float(period)
        tri = 1 - abs(2 * ph - 1)                 # 0 → 1 → 0
        lo = base + slope * i + tri * (width - 2)
        bars.append({"date": "d%04d" % i, "o": lo + 1, "h": lo + 2, "l": lo, "c": lo + 1})
    return bars


def cols(bars):
    return [b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars]


class TestDraw(unittest.TestCase):
    def test_pivot_needs_right_side(self):
        v = [5, 4, 3, 2, 1, 2, 3, 4, 5, 6, 7]
        f = T.pivot_flags(v, k=3)
        self.assertTrue(f[4])
        self.assertFalse(any(f[:4]) or any(f[5:]))
        self.assertFalse(T.pivot_flags(v[:6], k=3)[4])          # 오른쪽 3봉이 없으면 아직 저점이 아니다

    def test_up_channel_recovers_lines(self):
        bars = zigzag()
        h, l, c = cols(bars)
        t = 199                                                  # 창이 저점(80번 봉)에서 시작하는 날
        ch = T.up_channel(h, l, c, t, 120, T.pivot_flags(l))
        self.assertIsNotNone(ch)
        self.assertAlmostEqual(ch["slope"], 0.5, places=6)       # 저점-저점 기울기
        self.assertAlmostEqual(ch["width"], 20.0, places=6)      # ③고점까지 평행 이동한 폭
        self.assertEqual(l[ch["i1"]], min(l[t - 119:t + 1]))     # ① = 창 안 최저 저가
        self.assertGreaterEqual(ch["i2"] - ch["i1"], 30)         # ①~② 간격 ≥ N/4
        self.assertLessEqual(ch["i2"], t - T.PIVOT_K)            # ② 는 확정된 저점만
        self.assertEqual(ch["valid_to"], ch["i2"] + (ch["i2"] - ch["i1"]))   # 향후 1배
        self.assertAlmostEqual(T.mid(ch, t), (T.lower(ch, t) + T.upper(ch, t)) / 2)

    def test_no_channel_when_falling(self):
        bars = zigzag(slope=-0.5)
        h, l, c = cols(bars)
        self.assertIsNone(T.up_channel(h, l, c, 165, 120, T.pivot_flags(l)))
        dn = T.down_channel(h, l, c, 165, 120, T.pivot_flags(h, low=False))
        self.assertIsNotNone(dn)
        self.assertLess(dn["slope"], 0)                          # 하향은 고점-고점부터

    def test_intact_breaks_on_close_below(self):
        bars = zigzag()
        h, l, c = cols(bars)
        ch = T.up_channel(h, l, c, 199, 120, T.pivot_flags(l))
        self.assertTrue(T.intact(ch, c, 199))
        c2 = list(c)
        c2[198] = T.lower(ch, 198) - T.TOL * ch["width"] - 0.01  # 보조 폭을 넘는 종가 이탈
        self.assertFalse(T.intact(ch, c2, 199))
        c3 = list(c)
        c3[198] = T.lower(ch, 198) - T.TOL * ch["width"] + 0.01  # 살짝 튀어나간 것은 보조 — 이탈 아님
        self.assertTrue(T.intact(ch, c3, 199))
        self.assertFalse(T.intact(ch, c, ch["valid_to"] + 1))    # 유효 기간이 지나면 끝


class TestRule(unittest.TestCase):
    def test_buys_lower_sells_upper(self):
        tr = T.backtest(zigzag(n=400), "중", "base", 0.1)
        closed = [t for t in tr if t["why"] != "open"]
        self.assertTrue(closed)
        for t in closed:
            ch = t["ch"]
            self.assertLessEqual(t["entry"], T.mid(ch, t["entry_i"]))          # 하단 쪽에서 샀다
            if t["why"] == "target":
                self.assertGreaterEqual(t["exit"], T.upper(ch, t["exit_i"]) - 1e-9)
                self.assertGreater(t["net_pct"], 0)
        self.assertTrue(any(t["why"] == "target" for t in closed))

    def test_cost_is_subtracted(self):
        a = T.backtest(zigzag(n=400), "중", "base", 0.0)
        b = T.backtest(zigzag(n=400), "중", "base", 0.25)
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            self.assertAlmostEqual(x["net_pct"] - y["net_pct"], 0.25, places=9)

    def test_stop_exits_next_open(self):
        bars = zigzag(n=400)
        first = T.backtest(bars, "중", "base", 0.0)[0]
        k = first["entry_i"] + 1
        ch = first["ch"]
        crash = T.lower(ch, k) - ch["width"]
        for j in range(k, len(bars)):
            bars[j] = dict(bars[j], o=crash, h=crash + 1, l=crash - 1, c=crash)
        t0 = T.backtest(bars, "중", "base", 0.0)[0]
        self.assertEqual(t0["why"], "stop")
        self.assertEqual(t0["exit_i"], k + 1)                    # 종가 이탈 다음 날 시가
        self.assertEqual(t0["exit"], bars[k + 1]["o"])
        self.assertLess(t0["net_pct"], 0)

    def test_no_lookahead_on_real_bars(self):
        """실데이터: 앞쪽 봉만 줬을 때의 닫힌 거래는 전체를 줬을 때와 같아야 한다(뒤 봉이 과거 판단을 바꾸지 않는다)."""
        path = os.path.join(ROOT, "data", "bb_reentry", "KRX_069500.csv")
        with open(path, encoding="utf-8") as fp:
            bars = [{"date": r["date"], "o": float(r["o"]), "h": float(r["h"]), "l": float(r["l"]), "c": float(r["c"])}
                    for r in csv.DictReader(fp)]
        full = T.backtest(bars, "중", "base", 0.05)
        cut = 2500
        part = [t for t in T.backtest(bars[:cut], "중", "base", 0.05) if t["why"] != "open"]
        self.assertGreater(len(part), 10)
        for a, b in zip(part, full):
            self.assertEqual((a["entry_i"], a["exit_i"], a["why"]), (b["entry_i"], b["exit_i"], b["why"]))
            self.assertAlmostEqual(a["net_pct"], b["net_pct"], places=9)

    def test_constants_fixed(self):
        self.assertEqual(T.WINDOWS, {"소": 60, "중": 120, "대": 250})
        self.assertEqual((T.PIVOT_K, T.MIN_SPAN_DIV, T.VALID_MULT, T.TOL, T.MIN_WIDTH_PCT), (5, 4, 1.0, 0.10, 5.0))
        self.assertEqual(T.PRIMARY, ("중", "base"))

    def test_today_shape(self):
        s = T.today(zigzag(), "중")
        self.assertIn("lower", s["up"])
        self.assertLess(s["up"]["lower"], s["up"]["mid"])
        self.assertLess(s["up"]["mid"], s["up"]["upper"])


if __name__ == "__main__":
    unittest.main()
